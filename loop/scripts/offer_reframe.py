#!/usr/bin/env python3
"""
offer_reframe.py — Deep Offer Reframing Engine for AutoResearchClaw v2.7

Takes an offer description + optional audience context, pulls related intel
from Qdrant (audience language, signals, competitor angles), and generates
7 psychological reframes — each with:
  - Reframed offer statement
  - Why it works (principle/doctrine)
  - Suggested hook direction
  - Best awareness level match
  - Audience language evidence (if found)

Uses the full ACA + Schwartz + Hopkins + Whitman + Hormozi doctrine stack.

Usage (as library):
    from offer_reframe import reframe_offer
    result = reframe_offer("ACA health insurance", audience="uninsured Americans 25-55")

Usage (CLI):
    python offer_reframe.py --offer "ACA health insurance" --audience "uninsured Americans"
    python offer_reframe.py --offer "stimulus assistance" --with-intel
"""

import os, sys, json, argparse, logging
from pathlib import Path
from typing import Optional

import yaml

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("offer-reframe")

# ── Config ───────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"

def _load_config() -> dict:
    if CFG_PATH.exists():
        return yaml.safe_load(CFG_PATH.read_text()) or {}
    return {}

CFG = _load_config()

# ── Qdrant intel pull ────────────────────────────────────────────────────────

def _pull_audience_intel(offer: str, limit: int = 5) -> str:
    """Search Qdrant for audience language and signals related to the offer."""
    try:
        from qdrant_sink import search_similar, QDRANT_ENABLED
        if not QDRANT_ENABLED:
            return ""
        results = search_similar(offer, limit=limit)
        if not results:
            return ""
        lines = []
        for r in results:
            payload = r.payload or {}
            title = payload.get("title", payload.get("headline", ""))
            source = payload.get("source", "")
            text = payload.get("text", payload.get("summary", ""))[:200]
            if title or text:
                lines.append(f"[{source}] {title}: {text}")
        return "\n".join(lines)
    except Exception as e:
        log.warning(f"Qdrant pull failed (non-fatal): {e}")
        return ""


def _pull_concept_vault() -> str:
    """Load existing concepts from the vault for context."""
    vault_path = Path(__file__).parent.parent / "learnings" / "concept_vault.json"
    if not vault_path.exists():
        return ""
    try:
        concepts = json.loads(vault_path.read_text())
        lines = []
        for c in concepts:
            lines.append(f"- {c['concept_id']}: {c['name']} ({c['status']}) — {c.get('description', '')[:100]}")
        return "\n".join(lines)
    except Exception:
        return ""


def _pull_learnings() -> str:
    """Load recent learnings for context."""
    path = Path(__file__).parent.parent / "learnings" / "learnings.md"
    if not path.exists():
        return ""
    try:
        text = path.read_text()
        # Return last 500 chars (most recent learnings)
        return text[-500:] if len(text) > 500 else text
    except Exception:
        return ""


# ── Reframe system prompt ────────────────────────────────────────────────────

REFRAME_SYSTEM = """\
You are Rachel, an expert ad creative strategist trained in the full ACA (Ad Creative Academy) methodology, \
Schwartz's Breakthrough Advertising, Hopkins' Scientific Advertising, Whitman's CA$HVERTISING, and \
Hormozi's $100M Leads + $100M Offers.

Your task: REFRAME an offer 7 different ways. Same product/service, completely different psychological frames.

## The 7 Reframe Lenses

1. **GAIN REFRAME** — What they GET. Turn the offer into something they receive/claim/unlock.
   - Doctrine: Hormozi value equation, Whitman LF8 (survival, comfort)
   - Example: "$0 health insurance" → "$6,400 health subsidy you can claim"

2. **LOSS REFRAME** — What they're LOSING by not acting. Cost of inaction.
   - Doctrine: Kahneman loss aversion (2x stronger than gains), Whitman Fear Factor
   - Example: "Save money on insurance" → "You're leaving $6,400 on the table every year"

3. **IDENTITY REFRAME** — Who they BECOME. Shame → empowerment. Outsider → insider.
   - Doctrine: Whitman Ego Morphing + Social Approval (LF8), Schwartz Identification technique
   - Example: "Cheap insurance for low-income" → "Smart Americans who claim what they're owed"

4. **SPECIFICITY REFRAME** — Vague → concrete number, timeframe, or mechanism.
   - Doctrine: Hopkins (specific beats vague, always), Schwartz Mechanization
   - Example: "Affordable coverage" → "$0/month premium with $6,400 annual subsidy"

5. **CATEGORY ESCAPE** — Reposition outside the obvious category entirely.
   - Doctrine: Schwartz Redefinition + Camouflage techniques
   - Example: "Health insurance plan" → "Government money-back program for families"

6. **MECHANISM REFRAME** — Lead with HOW it works, not what it is.
   - Doctrine: Schwartz Mechanization, Stage 2-3 Sophistication
   - Example: "Get insured" → "A 2-minute eligibility check that unlocks your federal subsidy"

7. **TRIGGER EVENT REFRAME** — Anchor to a specific life moment, not a product feature.
   - Doctrine: Hormozi $100M Offers (trigger event = strongest hook angle)
   - Example: "Enroll in ACA" → "Just lost your job? You qualify for emergency health coverage"

## Output Format

For EACH of the 7 reframes, output:

### [N]. [REFRAME TYPE]

**Reframed Offer:** [The new offer statement — this is the headline/hook seed]

**Why It Works:** [Which doctrine/principle and WHY it's psychologically stronger — 2-3 sentences max]

**Hook Direction:** [A specific hook you'd write for this frame — ready to test]

**Best Awareness Level:** [Unaware / Problem Aware / Solution Aware / Product Aware / Most Aware]

**Audience Evidence:** [If audience intel was provided, cite the specific verbatim language or signal that supports this frame. If none, write "No intel available — validate with audience research"]

---

After all 7, add:

## 🏆 TOP PICK

[Which reframe is strongest and why — 2 sentences. Include which to A/B test against which.]

## 🔗 REFRAME COMBINATIONS

[Suggest 2-3 power combinations where you stack reframes together. Example: Gain + Specificity = "$6,400 subsidy" is both a gain frame AND specific.]

Be ruthless. Be specific. No filler. Every reframe must be different enough to be its own ad concept.
"""


# ── Main reframe function ───────────────────────────────────────────────────

def reframe_offer(
    offer: str,
    audience: str = "",
    current_angle: str = "",
    with_intel: bool = True,
    config: Optional[dict] = None,
) -> str:
    """
    Generate 7 psychological reframes for an offer.

    Args:
        offer: The offer/product description
        audience: Target audience description (optional)
        current_angle: What angle is currently being used (optional)
        with_intel: Whether to pull Qdrant audience intel
        config: Override config dict

    Returns:
        Formatted reframe analysis as string
    """
    cfg = config or CFG

    # Gather context
    intel_text = ""
    if with_intel:
        intel_text = _pull_audience_intel(offer)

    concepts_text = _pull_concept_vault()
    learnings_text = _pull_learnings()

    # Build user prompt
    user_parts = [f"## OFFER\n{offer}"]

    if audience:
        user_parts.append(f"\n## TARGET AUDIENCE\n{audience}")

    if current_angle:
        user_parts.append(f"\n## CURRENT ANGLE (what's already running)\n{current_angle}")

    if intel_text:
        user_parts.append(f"\n## AUDIENCE INTEL (from Qdrant — real signals & language)\n{intel_text}")

    if concepts_text:
        user_parts.append(f"\n## EXISTING CONCEPTS IN VAULT\n{concepts_text}")

    if learnings_text:
        user_parts.append(f"\n## RECENT LEARNINGS\n{learnings_text}")

    user_parts.append("\nGenerate 7 reframes now. Be specific to THIS offer — no generic advice.")

    user_prompt = "\n".join(user_parts)

    # Call LLM
    try:
        from agent_runner import run_prompt
        result = run_prompt(REFRAME_SYSTEM, user_prompt, max_tokens=4000, config=cfg)
        return result
    except Exception as e:
        log.error(f"LLM call failed: {e}")
        return f"⚠️ Reframe generation failed: {str(e)[:200]}"


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Offer Reframe Engine")
    parser.add_argument("--offer", required=True, help="Offer description")
    parser.add_argument("--audience", default="", help="Target audience")
    parser.add_argument("--current-angle", default="", help="Current angle being used")
    parser.add_argument("--with-intel", action="store_true", default=True,
                        help="Pull audience intel from Qdrant (default: true)")
    parser.add_argument("--no-intel", action="store_true", help="Skip Qdrant intel pull")
    parser.add_argument("--output", default="", help="Output file path")
    args = parser.parse_args()

    result = reframe_offer(
        offer=args.offer,
        audience=args.audience,
        current_angle=args.current_angle,
        with_intel=not args.no_intel,
    )

    if args.output:
        Path(args.output).write_text(result)
        print(f"Wrote reframe to {args.output}")
    else:
        print(result)


if __name__ == "__main__":
    main()

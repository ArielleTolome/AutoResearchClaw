"""
prediction_scorer.py — Pre-deploy creative prediction scoring.

Scores a challenger creative across 5 dimensions before it hits the approval gate.
Returns predicted hook rate range + overall confidence score.

Doctrine:
  "The first version is never the best version" — Rachel SOUL.md
  But some first versions are better than others. Score before you spend.

  This is NOT a replacement for live testing. It's a pre-filter:
  - Catch obvious structural problems before spending $50 in testing budget
  - Rank multiple challengers when generate.py produces variants
  - Give the approval gate context ("predicted hook rate: 28-35%")

Scoring dimensions (25 points total, matching ACA Hook Scoring Rubric):
  1. Hook Clarity       (0-5)  — 3-second clarity test: does it instantly communicate?
  2. Tension/Desire     (0-5)  — does it agitate pain OR amplify desire? (not both needed — pick one)
  3. Angle Freshness    (0-5)  — fatigue score inverted: FRESH=5, WARMING=3, SATURATED=1, DEAD=0
  4. Awareness Match    (0-5)  — does copy match the offer's Schwartz awareness level?
  5. Pattern Interrupt  (0-5)  — stops the scroll. Unexpected, specific, visual, or counterintuitive?

Predicted hook rate mapping:
  22-25 → 35-45% (Unicorn territory)
  18-21 → 25-35% (Strong)
  14-17 → 15-25% (Average)
  10-13 → 8-15%  (Weak — iterate before deploying)
  0-9   → <8%    (Kill — do not deploy)

Note: Hook rate predictions are directional, not precise. ±10pp error is expected.
Use as a ranking tool (which challenger is better?) more than an absolute forecast.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import anthropic
import yaml

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
PROMPTS_PATH = ROOT / "config" / "prompts.yaml"

# ---------------------------------------------------------------------------
# Awareness stage descriptors (Schwartz 1-5)
# ---------------------------------------------------------------------------
AWARENESS_DESCRIPTORS = {
    1: "Completely unaware of any problem or solution. Hook must address a universal desire or pain with no mention of the product category.",
    2: "Problem-aware but not solution-aware. Hook names the pain directly. No product mention. Drive curiosity about the solution.",
    3: "Solution-aware but not product-aware. Hook can reference the solution category. Must differentiate with a mechanism or specific outcome.",
    4: "Product-aware but not committed. Hook addresses objections, highlights unique value, or adds urgency. Comparison and proof heavy.",
    5: "Most aware — knows the product, just needs a reason to act now. Direct offer, price, discount, deadline.",
}

HOOK_RATE_RANGES = [
    (22, 25, "35–45%", "Unicorn territory 🦄"),
    (18, 21, "25–35%", "Strong ✅"),
    (14, 17, "15–25%", "Average — consider iterating"),
    (10, 13, "8–15%",  "Weak — iterate before deploying ⚠️"),
    (0,   9, "<8%",    "Kill — do not deploy 🔴"),
]


# ---------------------------------------------------------------------------
# LLM-based scoring
# ---------------------------------------------------------------------------

SCORER_SYSTEM_PROMPT = """You are an expert ad creative analyst trained on Ad Creative Academy methodology, Schwartz's Breakthrough Advertising, and 10+ years of direct response performance data.

You score ad creative copy on 5 dimensions. You output ONLY valid JSON. No markdown, no explanation outside the JSON.

JSON format:
{
  "hook_clarity": <0-5>,
  "tension_desire": <0-5>,
  "pattern_interrupt": <0-5>,
  "notes": {
    "hook_clarity": "<one sentence explaining score>",
    "tension_desire": "<one sentence explaining score>",
    "pattern_interrupt": "<one sentence explaining score>"
  }
}

Scoring criteria:

hook_clarity (0-5):
  5 = Instantly clear in 3 seconds who this is for and what they'll get/avoid. Zero ambiguity.
  4 = Clear with minor vagueness. Audience and outcome understood.
  3 = Mostly clear. One element is ambiguous (who? what? why now?).
  2 = Confusing. Requires re-reading. Too clever or too vague.
  1 = Opaque. Most readers won't understand what this is about.
  0 = Complete non-sequitur.

tension_desire (0-5):
  5 = Creates strong emotional pull — either acute pain agitation OR vivid desire amplification.
  4 = Good tension. Emotional trigger present. Could be stronger.
  3 = Some tension but generic ("save money", "feel better" — everyone says this).
  2 = Mild. Technically present but doesn't land emotionally.
  1 = Flat. No emotional hook.
  0 = Anti-emotional. Reads like a disclaimer.

pattern_interrupt (0-5):
  5 = Stops the scroll. Unexpected, counter-intuitive, hyper-specific, or visually vivid.
  4 = Interesting. Most people would pause.
  3 = Somewhat interesting. Doesn't blend in, but doesn't demand attention.
  2 = Generic. Seen a hundred like this.
  1 = Boring. Nothing to make anyone stop.
  0 = Invisible. Scrolls right past it."""


def score_with_llm(
    hook: str,
    body: str,
    cta: str,
    config: dict,
) -> dict:
    """Call Claude to score the 3 LLM-evaluated dimensions."""
    client = anthropic.Anthropic(api_key=config["llm"]["api_key"])

    full_copy = f"HOOK: {hook}\n\nBODY: {body}\n\nCTA: {cta}"

    offer_context = (
        f"Offer: {config['offer']['name']}\n"
        f"Niche: {config['offer'].get('niche', '')}\n"
        f"Awareness Stage: {config['offer'].get('awareness_stage', 3)} "
        f"({AWARENESS_DESCRIPTORS.get(config['offer'].get('awareness_stage', 3), '')})\n"
        f"Platform: {config['offer'].get('platform', 'meta')}\n"
    )

    user_prompt = f"{offer_context}\n\nAD COPY TO SCORE:\n{full_copy}"

    message = client.messages.create(
        model=config["llm"]["model"],
        max_tokens=512,
        system=SCORER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("```").strip()

    return json.loads(raw)


def score_awareness_match(copy_text: str, awareness_stage: int) -> tuple[int, str]:
    """
    Rule-based awareness match scoring.
    Checks if copy signals match the declared Schwartz awareness stage.
    Returns (score 0-5, note).
    """
    text_lower = copy_text.lower()

    # Stage 1 (Unaware) — should NOT mention insurance/product, should mention universal desire
    if awareness_stage == 1:
        product_mentions = any(w in text_lower for w in ["insurance", "policy", "premium", "quote", "coverage"])
        universal_desire = any(w in text_lower for w in ["save", "money", "family", "protect", "safe", "stress", "worry", "bills"])
        if not product_mentions and universal_desire:
            return 5, "Correctly avoids product mention for unaware audience"
        elif product_mentions:
            return 1, "Stage 1 copy should NOT mention the product category — audience doesn't know they need it yet"
        else:
            return 3, "Avoids product but lacks strong universal desire hook"

    # Stage 2 (Problem-aware) — names the pain, no product pitch
    elif awareness_stage == 2:
        pain_present = any(w in text_lower for w in ["rate", "expensive", "overcharged", "paying too much", "cant afford", "problem", "struggle", "frustrat"])
        hard_sell = any(w in text_lower for w in ["buy now", "get a quote", "sign up today", "limited time"])
        if pain_present and not hard_sell:
            return 5, "Good — names the problem without jumping to a hard sell"
        elif hard_sell:
            return 2, "Stage 2 copy shouldn't hard-sell — audience isn't solution-aware yet"
        else:
            return 3, "Pain mention weak — be more specific about the problem"

    # Stage 3 (Solution-aware) — differentiates mechanism
    elif awareness_stage == 3:
        mechanism = any(w in text_lower for w in ["how", "why", "because", "works by", "unlike", "different", "the only", "discover", "new way"])
        if mechanism:
            return 5, "Good mechanism differentiation for solution-aware audience"
        else:
            return 3, "Stage 3 copy should differentiate the mechanism — why is this solution different?"

    # Stage 4 (Product-aware) — objection handling + proof
    elif awareness_stage == 4:
        proof = any(w in text_lower for w in ["proven", "customers", "reviews", "rated", "trusted", "guarantee", "unlike", "compare"])
        if proof:
            return 5, "Good proof/comparison for product-aware audience"
        else:
            return 3, "Stage 4 copy should include proof or comparison to address objections"

    # Stage 5 (Most aware) — direct offer
    elif awareness_stage == 5:
        urgency = any(w in text_lower for w in ["today", "now", "limited", "expires", "save", "off", "%", "free"])
        if urgency:
            return 5, "Good direct offer with urgency for most-aware audience"
        else:
            return 3, "Stage 5 copy should have a clear, direct offer with urgency"

    return 3, "Awareness stage not set — unable to evaluate match"


def score_angle_freshness(angle: str, fatigue_results: dict) -> tuple[int, str]:
    """
    Convert angle fatigue tier to a freshness score.
    Returns (score 0-5, note).
    """
    if not angle or not fatigue_results:
        return 3, "No angle fatigue data — defaulting to neutral"

    angle_data = fatigue_results.get(angle.lower().replace(" ", "_"), {})
    tier = angle_data.get("tier", "")

    tier_scores = {
        "FRESH": (5, f"🟢 '{angle}' has no competition — first-mover advantage"),
        "WARMING": (3, f"🟡 '{angle}' is gaining traction — still a good window"),
        "SATURATED": (1, f"🟠 '{angle}' is crowded — strong differentiation required"),
        "DEAD": (0, f"🔴 '{angle}' is overdone — rotate to a fresh angle"),
    }

    return tier_scores.get(tier, (3, f"No fatigue data for angle '{angle}'"))


def predict_hook_rate(total_score: int) -> tuple[str, str]:
    for low, high, rate_range, label in HOOK_RATE_RANGES:
        if low <= total_score <= high:
            return rate_range, label
    return "<8%", "Kill — do not deploy 🔴"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def score_creative(
    hook: str,
    body: str,
    cta: str,
    angle: str = "",
    fatigue_results: dict | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Score a challenger creative across all 5 dimensions.

    Args:
        hook: The opening hook line(s) — first 3 seconds
        body: Body copy
        cta: Call to action
        angle: The angle label (e.g. 'loyalty_betrayal') for fatigue lookup
        fatigue_results: Output from angle_fatigue.score_angle_fatigue()
        dry_run: Skip LLM call, return mock scores

    Returns dict with full breakdown + prediction.
    """
    config = yaml.safe_load((ROOT / "config" / "config.yaml").read_text())
    awareness_stage = config.get("offer", {}).get("awareness_stage", 3)

    if dry_run:
        print("[SCORER] DRY RUN — returning mock prediction")
        return _mock_score()

    # --- Dimension 3: Awareness Match (rule-based) ---
    full_copy = f"{hook} {body} {cta}"
    awareness_score, awareness_note = score_awareness_match(full_copy, awareness_stage)

    # --- Dimension 2: Angle Freshness ---
    freshness_score, freshness_note = score_angle_freshness(angle, fatigue_results or {})

    # --- Dimensions 1, 4, 5: LLM-evaluated ---
    print("[SCORER] Calling LLM for creative scoring...")
    llm_scores = score_with_llm(hook, body, cta, config)

    hook_clarity = llm_scores.get("hook_clarity", 3)
    tension_desire = llm_scores.get("tension_desire", 3)
    pattern_interrupt = llm_scores.get("pattern_interrupt", 3)
    notes = llm_scores.get("notes", {})

    total_score = hook_clarity + tension_desire + freshness_score + awareness_score + pattern_interrupt
    predicted_rate, rate_label = predict_hook_rate(total_score)

    result = {
        "total_score": total_score,
        "max_score": 25,
        "predicted_hook_rate": predicted_rate,
        "prediction_label": rate_label,
        "dimensions": {
            "hook_clarity": {
                "score": hook_clarity,
                "note": notes.get("hook_clarity", ""),
            },
            "tension_desire": {
                "score": tension_desire,
                "note": notes.get("tension_desire", ""),
            },
            "angle_freshness": {
                "score": freshness_score,
                "note": freshness_note,
            },
            "awareness_match": {
                "score": awareness_score,
                "note": awareness_note,
            },
            "pattern_interrupt": {
                "score": pattern_interrupt,
                "note": notes.get("pattern_interrupt", ""),
            },
        },
        "angle": angle,
        "awareness_stage": awareness_stage,
        "recommendation": _score_recommendation(total_score, llm_scores, freshness_score, awareness_score),
    }

    # Print summary
    print(f"\n[SCORER] ─── Creative Prediction ───────────────────────")
    print(f"  Total Score:     {total_score}/25")
    print(f"  Predicted HR:    {predicted_rate}  ({rate_label})")
    print(f"  Hook Clarity:    {hook_clarity}/5  — {notes.get('hook_clarity','')[:60]}")
    print(f"  Tension/Desire:  {tension_desire}/5  — {notes.get('tension_desire','')[:60]}")
    print(f"  Angle Freshness: {freshness_score}/5  — {freshness_note[:60]}")
    print(f"  Awareness Match: {awareness_score}/5  — {awareness_note[:60]}")
    print(f"  Pattern Inter.:  {pattern_interrupt}/5  — {notes.get('pattern_interrupt','')[:60]}")
    print(f"  Recommendation:  {result['recommendation']}")
    print(f"─────────────────────────────────────────────────────────\n")

    return result


def format_score_for_discord(score_result: dict) -> str:
    """Format prediction as Discord embed field content."""
    dims = score_result["dimensions"]
    emoji_map = {5: "🟢", 4: "🟢", 3: "🟡", 2: "🟠", 1: "🔴", 0: "🔴"}

    lines = [
        f"**Score: {score_result['total_score']}/25** — {score_result['prediction_label']}",
        f"Predicted Hook Rate: **{score_result['predicted_hook_rate']}**",
        "",
    ]
    for dim_name, dim_data in dims.items():
        s = dim_data["score"]
        emoji = emoji_map.get(s, "⚪")
        label = dim_name.replace("_", " ").title()
        lines.append(f"{emoji} {label}: {s}/5")

    lines.append("")
    lines.append(f"_{score_result.get('recommendation', '')}_")

    return "\n".join(lines)


def _score_recommendation(total: int, llm_scores: dict, freshness: int, awareness: int) -> str:
    if total >= 22:
        return "Deploy immediately. Unicorn candidate."
    elif total >= 18:
        return "Deploy. Strong creative — monitor hook rate in first 500 impressions."
    elif total >= 14:
        # Find weakest dimension
        worst = min(
            [("hook_clarity", llm_scores.get("hook_clarity", 3)),
             ("tension_desire", llm_scores.get("tension_desire", 3)),
             ("pattern_interrupt", llm_scores.get("pattern_interrupt", 3)),
             ("angle_freshness", freshness),
             ("awareness_match", awareness)],
            key=lambda x: x[1]
        )
        return f"Average. Iterate on {worst[0].replace('_', ' ')} before deploying."
    elif total >= 10:
        return "Weak. Rewrite hook — don't deploy until score ≥ 18."
    else:
        return "Kill this creative. Fundamental structural problems."


def _mock_score() -> dict:
    return {
        "total_score": 19,
        "max_score": 25,
        "predicted_hook_rate": "25–35%",
        "prediction_label": "Strong ✅",
        "dimensions": {
            "hook_clarity": {"score": 4, "note": "Clear — auto insurance, price pain, immediate relevance"},
            "tension_desire": {"score": 4, "note": "Good pain agitation — 40% rate hike is specific and shocking"},
            "angle_freshness": {"score": 3, "note": "🟡 'pricing_pain' is warming — still a good window"},
            "awareness_match": {"score": 4, "note": "Matches stage 2 — names the problem without hard selling"},
            "pattern_interrupt": {"score": 4, "note": "Specific dollar amount + time frame creates scroll-stop"},
        },
        "angle": "pricing_pain",
        "awareness_stage": 2,
        "recommendation": "Deploy. Strong creative — monitor hook rate in first 500 impressions.",
    }


if __name__ == "__main__":
    # Quick CLI test
    result = score_creative(
        hook="Your neighbor just switched auto insurance and saved $847 this year. You're still paying the loyalty tax.",
        body="Insurance companies raise rates every year for loyal customers while giving their best deals to new sign-ups. After 12 years and zero claims, I got hit with a 34% rate increase. Spent 8 minutes comparing quotes. Switched. Same coverage, $70/month less.",
        cta="See if you're being overcharged →",
        angle="loyalty_betrayal",
        dry_run=True,
    )
    print(json.dumps(result, indent=2))

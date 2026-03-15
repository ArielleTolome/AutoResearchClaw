"""
generate.py — Write a new challenger creative using:
  - The current winner (new baseline)
  - The accumulated learnings log
  - Creative framework files (HOOKS.md, ANGLES.md, etc.)
  - The system prompt from prompts.yaml

Output: A structured challenger brief saved to learnings/runs/{ts}_challenger.md
"""

import yaml
import anthropic
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
PROMPTS_PATH = ROOT / "config" / "prompts.yaml"
BASELINE_PATH = ROOT / "config" / "baseline.md"
LEARNINGS_PATH = ROOT / "learnings" / "learnings.md"
RUNS_DIR = ROOT / "learnings" / "runs"

# Framework files to load as context
FRAMEWORK_FILES = [
    "HOOKS.md",
    "ANGLES.md",
    "ANALYSIS_WORKFLOW.md",
    "AWARENESS.md",
    "PSYCHOLOGY.md",
]


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def load_prompts():
    with open(PROMPTS_PATH) as f:
        return yaml.safe_load(f)


def load_frameworks(config: dict) -> str:
    """Load creative framework files as context."""
    frameworks_path = Path(config.get("frameworks_path", "../creative"))
    if not frameworks_path.is_absolute():
        frameworks_path = ROOT / frameworks_path

    chunks = []
    for fname in FRAMEWORK_FILES:
        fpath = frameworks_path / fname
        if fpath.exists():
            content = fpath.read_text()[:3000]  # cap per file to avoid token blowout
            chunks.append(f"=== {fname} ===\n{content}")
        else:
            print(f"  [WARN] Framework file not found: {fpath}")

    return "\n\n".join(chunks)


def load_learnings() -> str:
    if LEARNINGS_PATH.exists():
        return LEARNINGS_PATH.read_text()
    return "No learnings yet. This is the first cycle."


def load_baseline(winner: dict | None = None) -> str:
    """
    If we have a winner from this cycle, use its ad name as context.
    Otherwise fall back to the static baseline.md.
    """
    baseline = BASELINE_PATH.read_text()
    if winner:
        baseline += f"\n\n## Current Winner\nAd: {winner['ad_name']}\n"
        baseline += f"Hook Rate: {winner.get('hook_rate', 0):.0%} | CTR: {winner.get('ctr', 0):.1%} | CVR: {winner.get('cvr', 0):.1%} | CPA: ${winner.get('cpa') or '—'}"
    return baseline


def generate_challenger(winner: dict | None, dry_run: bool = False) -> str:
    config = load_config()
    prompts = load_prompts()

    print("[GENERATE] Loading frameworks and learnings...")
    frameworks = load_frameworks(config)
    learnings = load_learnings()
    baseline = load_baseline(winner)

    offer = config["offer"]
    user_prompt = (
        f"OFFER: {offer['name']}\n"
        f"NICHE: {offer['niche']}\n"
        f"AWARENESS STAGE: {offer['awareness_stage']} (Schwartz scale)\n"
        f"TARGET CPA: ${offer['target_cpa']}\n\n"
        f"--- CURRENT BASELINE ---\n{baseline}\n\n"
        f"--- ACCUMULATED LEARNINGS ---\n{learnings}\n\n"
        f"--- CREATIVE FRAMEWORKS ---\n{frameworks}\n\n"
        "Write the next challenger creative. Make ONE meaningful change. "
        "Ground it in the learnings. Don't repeat what's already failed."
    )

    if dry_run:
        print("[GENERATE] DRY RUN — skipping LLM call")
        challenger = _mock_challenger()
    else:
        print("[GENERATE] Calling LLM for challenger copy...")
        client = anthropic.Anthropic(api_key=config["llm"]["api_key"])
        message = client.messages.create(
            model=config["llm"]["model"],
            max_tokens=2048,
            system=prompts["generate"],
            messages=[{"role": "user", "content": user_prompt}],
        )
        challenger = message.content[0].text

    print(f"\n{'='*60}\nCHALLENGER GENERATED:\n{'='*60}\n{challenger}\n{'='*60}\n")

    # Save
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RUNS_DIR / f"{ts}_challenger.md"
    out_path.write_text(challenger)
    print(f"[GENERATE] Saved to {out_path}")

    return challenger


def _mock_challenger() -> str:
    return """CHALLENGER NAME: hook-question-rate-hike-v1
HYPOTHESIS: The baseline hook is a statement. Testing a question hook (type #9) targeting the "rate hike outrage" angle to see if Stage 3 awareness performs better than the current Stage 2 play.
ANGLE: Rate Hike Outrage (Angle #4 — Problem-Aware Agitation)
HOOK TYPE: Question Hook (#9)
AWARENESS STAGE: 3

--- HOOK ---
Did your car insurance just quietly go up — without you filing a single claim?

--- BODY ---
Most people don't notice until renewal. Insurance companies raise rates industry-wide, and loyal customers get hit hardest.
Takes 2 minutes to compare. Most drivers find better coverage for less.

--- CTA ---
See what your neighbors are paying →

--- FORMAT ---
Type: static (image: frustrated person opening insurance bill)
Length: N/A
Persona: N/A (no presenter — copy-forward)

--- STRATEGIC RATIONALE ---
Previous baseline used a loyalty-tax statement hook (Stage 2). Testing question hook at Stage 3 (solution-aware) because learnings show our audience already knows they can switch — they just haven't felt enough urgency. Question creates cognitive dissonance. "Rate hike outrage" angle hasn't been tested yet. CTA uses social proof framing ("neighbors") which PSYCHOLOGY.md confirms activates bandwagon principle."""


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("--winner-file", help="Path to analysis JSON with winner")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    winner = None
    if args.winner_file:
        data = json.loads(Path(args.winner_file).read_text())
        winner = data.get("winner")

    generate_challenger(winner, dry_run=args.dry_run)

"""
analyze.py — Compare baseline vs. challenger performance. Pick winner. Log learnings.

Logic:
  1. Score each ad on a composite metric (weighted: hook_rate 30%, CVR 40%, CPA 30%)
  2. Apply kill rules — flag underperformers for pausing
  3. Call LLM to write qualitative learnings
  4. Append to learnings/learnings.md
  5. Return winner ad dict + kill list
"""

import json
import yaml
import anthropic
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
PROMPTS_PATH = ROOT / "config" / "prompts.yaml"
LEARNINGS_PATH = ROOT / "learnings" / "learnings.md"
RUNS_DIR = ROOT / "learnings" / "runs"


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def load_prompts():
    with open(PROMPTS_PATH) as f:
        return yaml.safe_load(f)


def composite_score(ad: dict, config: dict) -> float:
    """
    Weighted composite score for ranking ads.
    Higher = better.
    Handles missing CPA (None) gracefully.
    """
    target_cpa = config["offer"]["target_cpa"]
    hook_w, cvr_w, cpa_w = 0.30, 0.40, 0.30

    hook_score = min(ad.get("hook_rate", 0) / 0.40, 1.0)   # normalize to unicorn (40%)
    cvr_score = min(ad.get("cvr", 0) / 0.05, 1.0)           # normalize to 5% CVR
    cpa = ad.get("cpa")
    cpa_score = min(target_cpa / cpa, 1.0) if cpa and cpa > 0 else 0.0

    return (hook_w * hook_score) + (cvr_w * cvr_score) + (cpa_w * cpa_score)


def apply_kill_rules(ads: list[dict], config: dict) -> list[str]:
    """Return list of ad_ids that should be paused."""
    rules = config["kill_rules"]
    target_cpa = config["offer"]["target_cpa"]
    kill_list = []

    for ad in ads:
        reasons = []
        if ad.get("hook_rate", 1) < rules["hook_rate_min"]:
            reasons.append(f"hook_rate {ad['hook_rate']:.0%} < {rules['hook_rate_min']:.0%}")
        if ad.get("ctr", 1) < rules["ctr_min"]:
            reasons.append(f"CTR {ad['ctr']:.1%} < {rules['ctr_min']:.1%}")
        cpa = ad.get("cpa")
        if cpa and cpa > target_cpa * rules["cpa_max_multiplier"]:
            reasons.append(f"CPA ${cpa:.2f} > {rules['cpa_max_multiplier']}x target (${target_cpa * rules['cpa_max_multiplier']:.2f})")

        if reasons:
            kill_list.append(ad["ad_id"])
            print(f"  💀 KILL {ad['ad_name']}: {'; '.join(reasons)}")

    return kill_list


def format_ads_for_prompt(ads: list[dict]) -> str:
    lines = []
    for ad in ads:
        lines.append(
            f"Ad: {ad['ad_name']}\n"
            f"  Hook Rate: {ad.get('hook_rate', 0):.0%} | CTR: {ad.get('ctr', 0):.1%} | "
            f"CVR: {ad.get('cvr', 0):.1%} | CPA: ${ad.get('cpa') or '—'} | CPM: ${ad.get('cpm', 0):.2f}"
        )
    return "\n".join(lines)


def call_llm_analysis(ads: list[dict], winner: dict, kill_list: list[str], config: dict, prompts: dict) -> str:
    """Ask Claude to write qualitative learnings."""
    client = anthropic.Anthropic(api_key=config["llm"]["api_key"])

    ad_summary = format_ads_for_prompt(ads)
    kill_names = [a["ad_name"] for a in ads if a["ad_id"] in kill_list]
    winner_name = winner["ad_name"] if winner else "none"

    message = client.messages.create(
        model=config["llm"]["model"],
        max_tokens=1024,
        system=prompts["analyze"],
        messages=[{
            "role": "user",
            "content": (
                f"Offer: {config['offer']['name']} | Target CPA: ${config['offer']['target_cpa']}\n\n"
                f"AD PERFORMANCE:\n{ad_summary}\n\n"
                f"WINNER (by composite score): {winner_name}\n"
                f"KILL LIST: {', '.join(kill_names) or 'none'}\n\n"
                "Write the analysis and learnings."
            ),
        }],
    )
    return message.content[0].text


def append_to_learnings(cycle_num: int, winner: dict, analysis: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = (
        f"\n---\n\n"
        f"### Cycle {cycle_num} — {ts}\n\n"
        f"**Winner:** {winner['ad_name'] if winner else 'No winner (all killed)'}\n\n"
        f"{analysis}\n"
    )
    with open(LEARNINGS_PATH, "a") as f:
        f.write(entry)
    print(f"[ANALYZE] Appended cycle {cycle_num} learnings to learnings.md")


def get_cycle_number() -> int:
    runs = list(RUNS_DIR.glob("*_harvest.json"))
    return len(runs)


def analyze(ads: list[dict], dry_run: bool = False) -> dict:
    config = load_config()
    prompts = load_prompts()

    print(f"[ANALYZE] Scoring {len(ads)} ads")

    # Score and rank
    for ad in ads:
        ad["_score"] = composite_score(ad, config)

    ranked = sorted(ads, key=lambda x: x["_score"], reverse=True)
    winner = ranked[0] if ranked else None

    if winner:
        print(f"  🏆 Winner: {winner['ad_name']} (score={winner['_score']:.3f})")

    # Kill rules
    kill_list = apply_kill_rules(ads, config)

    if dry_run:
        print("[ANALYZE] DRY RUN — skipping LLM call and learnings write")
        return {"winner": winner, "kill_list": kill_list, "analysis": "DRY RUN"}

    # LLM analysis
    print("[ANALYZE] Calling LLM for qualitative analysis...")
    analysis = call_llm_analysis(ads, winner, kill_list, config, prompts)
    print(f"\n{analysis}\n")

    # Log to learnings
    cycle_num = get_cycle_number()
    append_to_learnings(cycle_num, winner, analysis)

    # Save run result
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_path = RUNS_DIR / f"{ts}_analysis.json"
    run_path.write_text(json.dumps({
        "cycle": cycle_num,
        "winner": winner,
        "kill_list": kill_list,
        "ranked": ranked,
    }, indent=2))

    return {"winner": winner, "kill_list": kill_list, "analysis": analysis}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--harvest-file", help="Path to harvest JSON to analyze")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.harvest_file:
        ads = json.loads(Path(args.harvest_file).read_text())
    else:
        # Use mock data
        from harvest import mock_harvest_data
        ads = mock_harvest_data()

    result = analyze(ads, dry_run=args.dry_run)
    print(json.dumps({k: v for k, v in result.items() if k != "analysis"}, indent=2))

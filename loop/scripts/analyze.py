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


def store_to_qdrant(cycle_num: int, winner: dict, analysis: str, config: dict):
    """Store cycle learnings into the configured Qdrant collection."""
    import uuid
    from openai import OpenAI
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct

    qdrant_cfg = config.get("qdrant", {})
    qdrant_url = qdrant_cfg.get("url", "http://37.27.228.106:6333")
    collection = qdrant_cfg.get("collection", "rachel-memories")
    openai_key = qdrant_cfg.get("openai_api_key", "")

    if not openai_key:
        print("[ANALYZE] Qdrant enabled but no OpenAI API key configured; skipping store")
        return

    winner_name = winner["ad_name"] if winner else "No winner"
    memory_text = (
        f"[AutoLoop Cycle {cycle_num}] Offer: {config['offer']['name']} | "
        f"Platform: {config['offer'].get('platform', 'meta')} | "
        f"Winner: {winner_name} | "
        f"Learnings: {analysis[:500]}"
    )

    client = OpenAI(api_key=openai_key)
    embedding_resp = client.embeddings.create(
        model="text-embedding-3-small",
        input=memory_text,
    )
    vector = embedding_resp.data[0].embedding

    qdrant_client = QdrantClient(url=qdrant_url)
    qdrant_client.upsert(
        collection_name=collection,
        points=[PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "text": memory_text,
                "cycle": cycle_num,
                "offer": config["offer"]["name"],
                "winner": winner_name,
                "timestamp": datetime.now().isoformat(),
                "source": "auto_loop",
            },
        )],
    )
    print(f"[ANALYZE] Stored cycle {cycle_num} learnings to Qdrant ({collection})")


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
    if config.get("qdrant", {}).get("enabled", False):
        store_to_qdrant(cycle_num, winner, analysis, config)

    # Save run result
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_path = RUNS_DIR / f"{ts}_analysis.json"
    run_path.write_text(json.dumps({
        "cycle": cycle_num,
        "winner": winner,
        "kill_list": kill_list,
        "ranked": ranked,
    }, indent=2))

    result = {"winner": winner, "kill_list": kill_list, "analysis": analysis}

    # Intel analysis (multi-source)
    if config.get("intel_harvest", {}).get("enabled", False):
        intel_data = _load_latest_intel()
        if intel_data:
            intel_analysis = analyze_intel(intel_data, config)
            result["intel_analysis"] = intel_analysis

    return result


def _load_latest_intel() -> dict | None:
    """Load the most recent intel harvest JSON file."""
    intel_files = sorted(ROOT.glob("learnings/intel_*.json"), reverse=True)
    if not intel_files:
        print("[ANALYZE] No intel harvest files found")
        return None
    latest = intel_files[0]
    print(f"[ANALYZE] Loading intel from {latest.name}")
    return json.loads(latest.read_text())


def analyze_intel(intel_data: dict, config: dict) -> str:
    """
    Analyze competitive intel from multi-source harvest.
    Calls Claude to extract patterns from Foreplay, FB Ads, YouTube, and Anstrex data.
    """
    prompts = load_prompts()
    client = anthropic.Anthropic(api_key=config["llm"]["api_key"])

    # Build summary of top performers from each source
    sections = []

    # YouTube hooks
    yt_ads = intel_data.get("youtube", [])
    if yt_ads:
        hooks = "\n".join(
            f"- [{ad.get('title', '')}] Hook: {ad.get('hook_text', '')[:200]}"
            for ad in yt_ads[:10]
        )
        sections.append(f"## YouTube Hooks (first 60s transcripts)\n{hooks}")

    # Facebook Ads Library
    fb_ads = intel_data.get("fb_ads", [])
    if fb_ads:
        fb_sorted = sorted(fb_ads, key=lambda x: x.get("days_running", 0), reverse=True)
        fb_lines = "\n".join(
            f"- [{ad.get('page_name', '')}] ({ad.get('days_running', 0)}d) {ad.get('ad_body', '')[:200]}"
            for ad in fb_sorted[:10]
        )
        sections.append(f"## Longest-Running FB Ads\n{fb_lines}")

    # Foreplay
    fp_ads = intel_data.get("foreplay", [])
    if fp_ads:
        fp_60plus = [a for a in fp_ads if a.get("running_days", 0) >= 60]
        fp_list = fp_60plus[:10] if fp_60plus else fp_ads[:10]
        fp_lines = "\n".join(
            f"- [{ad.get('brand_name', '')}] ({ad.get('running_days', 0)}d) "
            f"Headline: {ad.get('headline', '')} | {ad.get('full_transcription', '')[:150]}"
            for ad in fp_list
        )
        sections.append(f"## Foreplay Ads (60+ days running)\n{fp_lines}")

    # Anstrex
    ax_ads = intel_data.get("anstrex", [])
    if ax_ads:
        ax_lines = "\n".join(
            f"- [{ad.get('advertiser', '')}] ({ad.get('days_running', 0)}d) {ad.get('title', '')}"
            for ad in ax_ads[:10]
        )
        sections.append(f"## Anstrex Native Headlines\n{ax_lines}")

    if not sections:
        print("[ANALYZE] No intel data to analyze")
        return ""

    intel_summary = "\n\n".join(sections)
    niche = config.get("offer", {}).get("niche", "general")

    user_prompt = (
        f"Niche: {niche}\n\n"
        f"COMPETITIVE INTEL DATA:\n\n{intel_summary}\n\n"
        "Analyze this competitive intelligence and extract:\n"
        "1. Top hook patterns from YouTube first-60s transcripts\n"
        "2. Longest-running FB ad copy themes (what keeps running = what works)\n"
        "3. Foreplay ad angles/headlines that have run 60+ days\n"
        "4. Anstrex native headlines if available\n"
        "5. Cross-source patterns: themes that appear in multiple sources\n\n"
        "Be specific. Extract exact phrases, structures, and angles we can adapt."
    )

    print("[ANALYZE] Calling LLM for intel analysis...")
    message = client.messages.create(
        model=config["llm"]["model"],
        max_tokens=2048,
        system=prompts.get("analyze", "You are an expert ad creative analyst."),
        messages=[{"role": "user", "content": user_prompt}],
    )
    analysis = message.content[0].text

    # Save to file
    ts = datetime.now().strftime("%Y%m%d")
    analysis_path = ROOT / "learnings" / f"intel_analysis_{ts}.md"
    analysis_path.write_text(f"# Intel Analysis — {ts}\n\n{analysis}\n")
    print(f"[ANALYZE] Intel analysis saved to {analysis_path}")

    return analysis


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

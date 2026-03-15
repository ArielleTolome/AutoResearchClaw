"""
AutoResearchClaw Orchestrator
=============================
Inspired by Karpathy's AutoResearch pattern.

The loop:
  1. HARVEST  — Pull Meta ad performance data
  2. ANALYZE  — Pick winner, apply kill rules, log learnings
  3. GENERATE — Write new challenger using learnings + creative frameworks
  4. DEPLOY   — Create challenger ad on Meta, pause losers
  5. NOTIFY   — Slack summary

Run modes:
  python orchestrator.py              # Full live run
  python orchestrator.py --dry-run    # Mock data, no API calls
  python orchestrator.py --step harvest|analyze|generate|deploy  # One step only
"""

import sys
import argparse
import yaml
import json
from pathlib import Path
from datetime import datetime

# Add scripts to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from harvest import harvest
from analyze import analyze
from generate import generate_challenger
from deploy import deploy

CONFIG_PATH = ROOT / "config" / "config.yaml"
RUNS_DIR = ROOT / "learnings" / "runs"
RUNS_DIR.mkdir(exist_ok=True)


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_latest_run_file(pattern: str) -> Path | None:
    files = sorted(RUNS_DIR.glob(pattern))
    return files[-1] if files else None


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def run_full_loop(dry_run: bool = False, adset_id: str = "", image_hash: str = "", link_url: str = ""):
    config = load_config()

    log("=" * 60)
    log(f"AutoResearchClaw — Starting cycle")
    log(f"Offer: {config['offer']['name']} | Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    log("=" * 60)

    # ── STEP 1: HARVEST ──────────────────────────────────────────────────────
    log("STEP 1: HARVEST")
    ads = harvest(dry_run=dry_run)

    if not ads:
        log("No ads with sufficient data. Skipping this cycle.")
        return

    log(f"Harvested {len(ads)} ads with enough data.")

    # ── STEP 2: ANALYZE ───────────────────────────────────────────────────────
    log("STEP 2: ANALYZE")
    analysis = analyze(ads, dry_run=dry_run)
    winner = analysis["winner"]
    kill_list = analysis["kill_list"]

    log(f"Winner: {winner['ad_name'] if winner else 'none'}")
    log(f"Kill list: {len(kill_list)} ads")

    # ── STEP 3: GENERATE ──────────────────────────────────────────────────────
    log("STEP 3: GENERATE")
    challenger_brief = generate_challenger(winner, dry_run=dry_run)

    # ── STEP 4: DEPLOY ────────────────────────────────────────────────────────
    log("STEP 4: DEPLOY")

    if not adset_id and not dry_run:
        log("WARNING: No --adset-id provided. Skipping deploy.")
        log("Re-run with --adset-id to deploy the challenger.")
        log(f"Challenger brief saved to: {get_latest_run_file('*_challenger.md')}")
        return

    result = deploy(
        challenger_brief=challenger_brief,
        kill_list=kill_list,
        adset_id=adset_id,
        image_hash=image_hash,
        link_url=link_url,
        dry_run=dry_run,
    )

    log("=" * 60)
    log(f"Cycle complete!")
    log(f"New challenger: {result['ad_name']}")
    log(f"Ads paused: {len(kill_list)}")
    log(f"Next cycle runs in {config['loop']['frequency_hours']}h")
    log("=" * 60)


def run_step(step: str, dry_run: bool, adset_id: str = "", image_hash: str = "", link_url: str = ""):
    """Run a single step for debugging."""
    if step == "harvest":
        ads = harvest(dry_run=dry_run)
        print(json.dumps(ads, indent=2))

    elif step == "analyze":
        harvest_file = get_latest_run_file("*_harvest.json")
        if not harvest_file:
            print("No harvest file found. Run 'harvest' step first.")
            return
        ads = json.loads(harvest_file.read_text())
        result = analyze(ads, dry_run=dry_run)
        print(json.dumps({k: v for k, v in result.items() if k != "analysis"}, indent=2))

    elif step == "generate":
        analysis_file = get_latest_run_file("*_analysis.json")
        winner = None
        if analysis_file:
            data = json.loads(analysis_file.read_text())
            winner = data.get("winner")
        generate_challenger(winner, dry_run=dry_run)

    elif step == "deploy":
        challenger_file = get_latest_run_file("*_challenger.md")
        analysis_file = get_latest_run_file("*_analysis.json")
        if not challenger_file:
            print("No challenger file found. Run 'generate' step first.")
            return
        brief = challenger_file.read_text()
        kill_list = []
        if analysis_file:
            data = json.loads(analysis_file.read_text())
            kill_list = data.get("kill_list", [])
        deploy(brief, kill_list, adset_id, image_hash, link_url, dry_run=dry_run)

    else:
        print(f"Unknown step: {step}. Choose from: harvest, analyze, generate, deploy")


def main():
    parser = argparse.ArgumentParser(description="AutoResearchClaw — Self-improving ad creative loop")
    parser.add_argument("--dry-run", action="store_true", help="Use mock data, no API calls")
    parser.add_argument("--step", choices=["harvest", "analyze", "generate", "deploy"],
                        help="Run a single step only")
    parser.add_argument("--adset-id", default="", help="Meta AdSet ID for deploying new ads")
    parser.add_argument("--image-hash", default="", help="Meta image hash for creative")
    parser.add_argument("--link-url", default="https://example.com", help="Destination URL")
    args = parser.parse_args()

    if args.step:
        run_step(args.step, args.dry_run, args.adset_id, args.image_hash, args.link_url)
    else:
        run_full_loop(args.dry_run, args.adset_id, args.image_hash, args.link_url)


if __name__ == "__main__":
    main()

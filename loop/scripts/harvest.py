"""
harvest.py — Pull Meta ad performance data for all active ads in the current test.

Metrics pulled:
  - impressions, spend, reach
  - inline_link_clicks (CTR numerator)
  - video_p25_watched_actions (hook rate proxy — 25% view)
  - video_p75_watched_actions (hold rate proxy)
  - actions (conversions)
  - cost_per_action_type (CPA)

Returns: List of ad performance dicts, saved to learnings/runs/{timestamp}_harvest.json
"""

import os
import json
import yaml
import requests
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
RUNS_DIR = ROOT / "learnings" / "runs"
RUNS_DIR.mkdir(exist_ok=True)


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_ad_insights(config, ad_id: str) -> dict:
    """Fetch insights for a single ad from Meta Marketing API."""
    token = config["meta"]["access_token"]
    window = config["loop"]["evaluation_window_days"]
    since = (datetime.now() - timedelta(days=window)).strftime("%Y-%m-%d")
    until = datetime.now().strftime("%Y-%m-%d")

    url = f"https://graph.facebook.com/v19.0/{ad_id}/insights"
    params = {
        "access_token": token,
        "time_range": json.dumps({"since": since, "until": until}),
        "fields": ",".join([
            "ad_name",
            "impressions",
            "reach",
            "spend",
            "inline_link_clicks",
            "video_p25_watched_actions",  # hook rate proxy
            "video_p75_watched_actions",  # hold rate proxy
            "actions",
            "cost_per_action_type",
            "cpm",
            "ctr",
        ]),
        "level": "ad",
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    return data[0] if data else {}


def get_active_ads(config) -> list[dict]:
    """Get all active ads in the ad account tagged for AutoResearchClaw."""
    token = config["meta"]["access_token"]
    account_id = config["meta"]["ad_account_id"]

    url = f"https://graph.facebook.com/v19.0/{account_id}/ads"
    params = {
        "access_token": token,
        "effective_status": '["ACTIVE", "PAUSED"]',
        "fields": "id,name,status,created_time",
        # Filter: only ads with "ARC_" prefix (AutoResearchClaw naming convention)
        "filtering": json.dumps([{"field": "ad.name", "operator": "CONTAIN", "value": "ARC_"}]),
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    return resp.json().get("data", [])


def compute_hook_rate(insights: dict) -> float:
    """Hook rate = 25% video views / impressions. Returns 0 if no video data."""
    impressions = int(insights.get("impressions", 0))
    if impressions == 0:
        return 0.0
    p25_views = 0
    for action in insights.get("video_p25_watched_actions", []):
        if action.get("action_type") == "video_view":
            p25_views = int(action.get("value", 0))
    return round(p25_views / impressions, 4)


def compute_conversions(insights: dict, conversion_event: str = "purchase") -> int:
    for action in insights.get("actions", []):
        if action.get("action_type") == conversion_event:
            return int(action.get("value", 0))
    return 0


def harvest(dry_run: bool = False) -> list[dict]:
    config = load_config()
    min_spend = config["loop"]["min_spend_threshold"]
    min_impressions = config["loop"]["min_impressions"]

    print(f"[HARVEST] Pulling active ARC_ ads from {config['meta']['ad_account_id']}")

    if dry_run:
        print("[HARVEST] DRY RUN — returning mock data")
        return mock_harvest_data()

    ads = get_active_ads(config)
    print(f"[HARVEST] Found {len(ads)} ARC_ ads")

    results = []
    for ad in ads:
        ad_id = ad["id"]
        insights = get_ad_insights(config, ad_id)

        spend = float(insights.get("spend", 0))
        impressions = int(insights.get("impressions", 0))

        if spend < min_spend or impressions < min_impressions:
            print(f"  SKIP {ad['name']} — not enough data (spend=${spend}, impressions={impressions})")
            continue

        hook_rate = compute_hook_rate(insights)
        conversions = compute_conversions(insights)
        clicks = int(insights.get("inline_link_clicks", 0))
        ctr = float(insights.get("ctr", 0)) / 100
        cpa = spend / conversions if conversions > 0 else None
        cvr = conversions / clicks if clicks > 0 else 0.0

        result = {
            "ad_id": ad_id,
            "ad_name": ad["name"],
            "status": ad["status"],
            "spend": spend,
            "impressions": impressions,
            "clicks": clicks,
            "ctr": round(ctr, 4),
            "hook_rate": hook_rate,
            "conversions": conversions,
            "cvr": round(cvr, 4),
            "cpa": round(cpa, 2) if cpa else None,
            "cpm": float(insights.get("cpm", 0)),
        }
        results.append(result)
        print(f"  ✓ {ad['name']} | hook={hook_rate:.0%} CTR={ctr:.1%} CVR={cvr:.1%} CPA=${cpa or '—'}")

    # Save run
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_path = RUNS_DIR / f"{ts}_harvest.json"
    run_path.write_text(json.dumps(results, indent=2))
    print(f"[HARVEST] Saved to {run_path}")

    return results


def mock_harvest_data() -> list[dict]:
    """Mock data for dry runs and testing."""
    return [
        {
            "ad_id": "123456789",
            "ad_name": "ARC_BASELINE_loyalty-tax-v1",
            "status": "ACTIVE",
            "spend": 45.20,
            "impressions": 8500,
            "clicks": 102,
            "ctr": 0.012,
            "hook_rate": 0.28,
            "conversions": 3,
            "cvr": 0.029,
            "cpa": 15.07,
            "cpm": 5.32,
        },
        {
            "ad_id": "987654321",
            "ad_name": "ARC_CHALLENGER_neighbor-discovery-v1",
            "status": "ACTIVE",
            "spend": 43.80,
            "impressions": 8200,
            "clicks": 147,
            "ctr": 0.018,
            "hook_rate": 0.38,
            "conversions": 6,
            "cvr": 0.041,
            "cpa": 7.30,
            "cpm": 5.34,
        },
    ]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    results = harvest(dry_run=args.dry_run)
    print(json.dumps(results, indent=2))

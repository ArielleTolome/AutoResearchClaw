"""
deploy.py — Create new challenger ad on Meta + pause losers.

Naming convention: ARC_{BASELINE|CHALLENGER}_{slug}_{cycle}
Example: ARC_CHALLENGER_hook-question-rate-hike-v1_005

This script:
  1. Parses the challenger brief (from generate.py output)
  2. Creates a new Meta ad with the challenger copy
  3. Pauses all ads in the kill list
  4. Sends Slack notification (if configured)
"""

import json
import re
import yaml
import requests
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
RUNS_DIR = ROOT / "learnings" / "runs"


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def parse_challenger_brief(brief: str) -> dict:
    """Extract structured fields from the challenger markdown."""
    def extract(pattern, text, default=""):
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else default

    return {
        "name": extract(r"CHALLENGER NAME:\s*(.+)", brief),
        "hypothesis": extract(r"HYPOTHESIS:\s*(.+)", brief),
        "angle": extract(r"ANGLE:\s*(.+)", brief),
        "hook_type": extract(r"HOOK TYPE:\s*(.+)", brief),
        "awareness_stage": extract(r"AWARENESS STAGE:\s*(.+)", brief),
        "hook": extract(r"--- HOOK ---\n(.+?)---", brief),
        "body": extract(r"--- BODY ---\n(.+?)---", brief),
        "cta": extract(r"--- CTA ---\n(.+?)---", brief),
        "format_type": extract(r"Type:\s*(.+)", brief),
        "rationale": extract(r"--- STRATEGIC RATIONALE ---\n(.+?)$", brief),
    }


def compose_ad_text(parsed: dict) -> str:
    """Combine hook + body + CTA into the Meta primary text field."""
    return f"{parsed['hook']}\n\n{parsed['body']}\n\n{parsed['cta']}"


def create_meta_ad(config: dict, adset_id: str, creative_id: str, ad_name: str) -> str:
    """Create the ad object. Returns new ad_id."""
    token = config["meta"]["access_token"]
    account_id = config["meta"]["ad_account_id"]

    url = f"https://graph.facebook.com/v19.0/{account_id}/ads"
    payload = {
        "access_token": token,
        "name": ad_name,
        "adset_id": adset_id,
        "creative": json.dumps({"creative_id": creative_id}),
        "status": "ACTIVE",
    }
    resp = requests.post(url, data=payload)
    resp.raise_for_status()
    return resp.json()["id"]


def create_meta_ad_creative(config: dict, ad_text: str, ad_name: str,
                             image_hash: str, link_url: str, headline: str) -> str:
    """Create ad creative. Returns creative_id."""
    token = config["meta"]["access_token"]
    account_id = config["meta"]["ad_account_id"]

    url = f"https://graph.facebook.com/v19.0/{account_id}/adcreatives"
    payload = {
        "access_token": token,
        "name": f"CREATIVE_{ad_name}",
        "object_story_spec": json.dumps({
            "page_id": config["meta"].get("page_id", ""),
            "link_data": {
                "message": ad_text,
                "link": link_url,
                "name": headline,  # headline field
                "image_hash": image_hash,
                "call_to_action": {
                    "type": "LEARN_MORE",
                },
            },
        }),
    }
    resp = requests.post(url, data=payload)
    resp.raise_for_status()
    return resp.json()["id"]


def pause_ad(config: dict, ad_id: str):
    """Pause a single ad."""
    token = config["meta"]["access_token"]
    url = f"https://graph.facebook.com/v19.0/{ad_id}"
    resp = requests.post(url, data={"access_token": token, "status": "PAUSED"})
    resp.raise_for_status()
    print(f"  ⏸  Paused ad {ad_id}")


def get_cycle_number() -> int:
    runs = list(RUNS_DIR.glob("*_harvest.json"))
    return len(runs)


def send_slack_notification(config: dict, message: str):
    webhook = config.get("notifications", {}).get("slack_webhook", "")
    if not webhook:
        return
    requests.post(webhook, json={"text": message})


def send_discord_notification(config: dict, message: str, embed: dict = None):
    """Post cycle summary to Discord channel via webhook."""
    webhook_url = config.get("notifications", {}).get("discord_webhook", "")
    if not webhook_url:
        return

    payload = {"content": message}
    if embed:
        payload["embeds"] = [embed]

    resp = requests.post(webhook_url, json=payload)
    if resp.status_code not in (200, 204):
        print(f"[DEPLOY] Discord notify failed: {resp.status_code}")
    else:
        print("[DEPLOY] Discord notification sent")


def build_discord_embed(parsed: dict, result: dict, kill_list: list, cycle: int, config: dict) -> dict:
    """Build a Discord embed card for the cycle summary."""
    color = 0x00FF88
    return {
        "title": f"🔄 AutoLoop Cycle {cycle} Complete",
        "color": color,
        "fields": [
            {"name": "Offer", "value": config["offer"]["name"], "inline": True},
            {"name": "Platform", "value": config["offer"].get("platform", "meta").upper(), "inline": True},
            {"name": "New Challenger", "value": f"`{result['ad_name']}`", "inline": False},
            {"name": "Angle", "value": parsed.get("angle", "—"), "inline": True},
            {"name": "Hook Type", "value": parsed.get("hook_type", "—"), "inline": True},
            {"name": "Awareness Stage", "value": parsed.get("awareness_stage", "—"), "inline": True},
            {"name": "Hypothesis", "value": parsed.get("hypothesis", "—")[:200], "inline": False},
            {"name": "Hook Preview", "value": f"\"{parsed.get('hook', '—')[:100]}\"", "inline": False},
            {"name": "Ads Killed", "value": str(len(kill_list)), "inline": True},
            {
                "name": "Status",
                "value": "⏳ Pending Approval" if config.get("approval_gate", {}).get("enabled") else "✅ Deployed",
                "inline": True,
            },
        ],
        "footer": {"text": "AutoResearchClaw v1.0"},
    }


def deploy(challenger_brief: str, kill_list: list[str],
           adset_id: str, image_hash: str, link_url: str,
           dry_run: bool = False) -> dict:
    config = load_config()
    cycle = get_cycle_number()

    parsed = parse_challenger_brief(challenger_brief)
    ad_name = f"ARC_CHALLENGER_{parsed['name']}_C{cycle:03d}"
    ad_text = compose_ad_text(parsed)
    headline = parsed["hook"][:60] if parsed["hook"] else "See if you're overpaying"

    print(f"[DEPLOY] Challenger: {ad_name}")
    print(f"[DEPLOY] Hypothesis: {parsed['hypothesis']}")
    print(f"\nAd copy preview:\n{'-'*40}\n{ad_text}\n{'-'*40}\n")

    if dry_run:
        print("[DEPLOY] DRY RUN — skipping Meta API calls")
        result = {"ad_id": "DRY_RUN", "ad_name": ad_name, "status": "dry_run"}
    else:
        # Create creative
        print("[DEPLOY] Creating Meta ad creative...")
        creative_id = create_meta_ad_creative(config, ad_text, ad_name, image_hash, link_url, headline)
        print(f"  ✓ Creative ID: {creative_id}")

        # Create ad
        print("[DEPLOY] Creating Meta ad...")
        new_ad_id = create_meta_ad(config, adset_id, creative_id, ad_name)
        print(f"  ✓ New Ad ID: {new_ad_id}")

        # Pause losers
        print(f"[DEPLOY] Pausing {len(kill_list)} loser ads...")
        for ad_id in kill_list:
            pause_ad(config, ad_id)

        result = {"ad_id": new_ad_id, "ad_name": ad_name, "status": "active"}

        # Notify
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        msg = (
            f"🚀 *AutoResearchClaw — Cycle {cycle}* [{ts}]\n"
            f"*New challenger deployed:* `{ad_name}`\n"
            f"*Hypothesis:* {parsed['hypothesis']}\n"
            f"*Angle:* {parsed['angle']} | *Hook Type:* {parsed['hook_type']}\n"
            f"*Paused ads:* {len(kill_list)}"
        )
        send_slack_notification(config, msg)
        print("[DEPLOY] Slack notification sent")
        send_discord_notification(
            config,
            "",
            embed=build_discord_embed(parsed, result, kill_list, cycle, config),
        )

    # Save run
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_path = RUNS_DIR / f"{ts}_deploy.json"
    run_path.write_text(json.dumps({
        "cycle": cycle,
        "challenger": parsed,
        "deployed": result,
        "killed": kill_list,
    }, indent=2))

    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--challenger-file", required=True, help="Path to challenger .md file")
    parser.add_argument("--kill-list", default="[]", help="JSON array of ad IDs to pause")
    parser.add_argument("--adset-id", default="", help="Meta AdSet ID to place new ad into")
    parser.add_argument("--image-hash", default="", help="Meta image hash for creative")
    parser.add_argument("--link-url", default="https://example.com", help="Destination URL")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    brief = Path(args.challenger_file).read_text()
    kill_list = json.loads(args.kill_list)

    deploy(brief, kill_list, args.adset_id, args.image_hash, args.link_url, dry_run=args.dry_run)

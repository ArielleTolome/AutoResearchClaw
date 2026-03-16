#!/usr/bin/env python3
"""
win_automation.py — AutoResearchClaw Win Automation

Closes the loop: Winner in creative_tracker → auto-creates iterate jobs in sprint queue.

Logic:
  1. Query creative_tracker (table 819) for Test Status = Winner (ID 3815)
  2. Load state from loop/state/win_automation_state.json
  3. For each NEW winner (not in state):
     a. Create Hook Swap + Actor Swap jobs via sprint_planner
     b. Post Discord embed announcing the win
     c. Add concept_id to processed list
  4. Save updated state

Usage:
  python win_automation.py              # Run normally
  python win_automation.py --dry-run   # Preview only, no writes
  python win_automation.py --reset     # Clear processed_winners state and re-run
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import requests
import yaml

# ── Module path setup (import sprint_planner as module) ──────────────────────
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
import sprint_planner as sp

# ── Config ────────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"
STATE_PATH = Path(__file__).parent.parent / "state" / "win_automation_state.json"
TRACKER_TABLE_ID = 819
BASEROW_URL = "https://baserow.pfsend.com"
BASEROW_EMAIL = "ariel@pigeonfi.com"
BASEROW_PASSWORD = "r4].Q2DtO}I88KWNcX)h"

# creative_tracker field IDs
F_CONCEPT_ID  = 8013
F_TEST_STATUS = 8016
F_HOOK        = 8018
F_OFFER       = 8027
F_ROAS        = 8028
F_CPA         = 8029
F_CTR         = 8031
F_HOOK_RATE   = 8032

WINNER_STATUS_ID = 3815


def _load_config() -> dict:
    if CFG_PATH.exists():
        return yaml.safe_load(CFG_PATH.read_text()) or {}
    return {}


def _get_jwt() -> str:
    cfg = _load_config()
    baserow_cfg = cfg.get("baserow", {})
    email = baserow_cfg.get("email", BASEROW_EMAIL)
    password = baserow_cfg.get("password", BASEROW_PASSWORD)
    resp = requests.post(
        f"{BASEROW_URL}/api/user/token-auth/",
        json={"email": email, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["token"]


def _headers() -> dict:
    return {"Authorization": f"JWT {_get_jwt()}", "Content-Type": "application/json"}


def _load_state() -> dict:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"processed_winners": [], "last_run": None}


def _save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.datetime.utcnow().isoformat()
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _get_winners() -> list:
    """Fetch all rows in creative_tracker where Test Status = Winner."""
    params = {
        "size": 200,
        f"filter__field_{F_TEST_STATUS}__link_row_has": WINNER_STATUS_ID,
    }
    # Use single_select filter syntax
    params = {"size": 200}
    resp = requests.get(
        f"{BASEROW_URL}/api/database/rows/table/{TRACKER_TABLE_ID}/",
        headers=_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json().get("results", [])

    # Filter locally: status must be Winner
    winners = []
    for row in rows:
        status = row.get(f"field_{F_TEST_STATUS}") or {}
        if isinstance(status, dict):
            status_id = status.get("id")
        else:
            status_id = None
        if status_id == WINNER_STATUS_ID:
            winners.append(row)

    return winners


def _post_win_embed(webhook_url: str, concept_id: str, hook: str, offer: str,
                    hook_rate: float, ctr: float, cpa: float,
                    job1_name: str, job2_name: str):
    """Post a winner announcement embed to Discord webhook."""
    hr_str = f"{hook_rate:.0%}" if hook_rate else "—"
    ctr_str = f"{ctr:.1%}" if ctr else "—"
    cpa_str = f"${cpa:.2f}" if cpa else "—"

    embed = {
        "title": "🏆 New Winner → 2 Jobs Created",
        "color": 0xF1C40F,
        "fields": [
            {
                "name": f"Concept {concept_id}",
                "value": f'"{hook[:80]}"' if hook else "—",
                "inline": False,
            },
            {
                "name": "Metrics",
                "value": f"HR: {hr_str} | CTR: {ctr_str} | CPA: {cpa_str}",
                "inline": False,
            },
            {
                "name": "Jobs Created",
                "value": f"→ `{job1_name}`\n→ `{job2_name}`",
                "inline": False,
            },
        ],
        "footer": {"text": f"AutoResearchClaw · {offer or 'Unknown Offer'}"},
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }

    payload = {"embeds": [embed]}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"⚠️  Discord webhook failed: {e}")


def process_winners(dry_run: bool = False, reset: bool = False):
    cfg = _load_config()
    win_cfg = cfg.get("win_automation", {})
    hook_swap_on_win = win_cfg.get("hook_swap_on_win", True)
    actor_swap_on_win = win_cfg.get("actor_swap_on_win", True)
    notify_discord = win_cfg.get("notify_discord", True)

    sprint_cfg = cfg.get("sprint", {})
    default_offer = sprint_cfg.get("default_offer", "Stimulus Assistance")
    default_offer_code = sprint_cfg.get("default_offer_code", "SA")
    default_channel = sprint_cfg.get("default_channel", "Meta")

    webhook_url = (cfg.get("notifications") or {}).get("discord_webhook") or \
                  (cfg.get("discord") or {}).get("webhook_url")

    state = _load_state()
    if reset:
        print("🔄 Resetting processed_winners state.")
        state["processed_winners"] = []

    processed = set(state.get("processed_winners", []))

    print("🔍 Fetching winners from creative_tracker…")
    winners = _get_winners()
    print(f"  Found {len(winners)} total winner(s).")

    new_count = 0
    for row in winners:
        concept_id = row.get(f"field_{F_CONCEPT_ID}") or ""
        if not concept_id:
            concept_id = f"row:{row['id']}"

        if concept_id in processed:
            print(f"  ⏭️  Already processed: {concept_id}")
            continue

        hook = row.get(f"field_{F_HOOK}") or ""
        offer = row.get(f"field_{F_OFFER}") or default_offer
        try:
            hook_rate = float(row.get(f"field_{F_HOOK_RATE}") or 0)
        except (TypeError, ValueError):
            hook_rate = 0.0
        try:
            ctr = float(row.get(f"field_{F_CTR}") or 0)
        except (TypeError, ValueError):
            ctr = 0.0
        try:
            cpa = float(row.get(f"field_{F_CPA}") or 0)
        except (TypeError, ValueError):
            cpa = 0.0

        # Derive offer code: use default_offer_code unless we can match
        offer_code = default_offer_code

        # Derive angle from hook (first 30 chars, cleaned)
        import re
        angle = re.sub(r"[^A-Za-z0-9 ]", "", hook[:30]).strip() or "WinnerAngle"

        print(f"\n  🏆 New winner: {concept_id} | Hook: {hook[:50]}")

        job1_name = "N/A (dry-run)"
        job2_name = "N/A (dry-run)"

        if dry_run:
            print(f"  [DRY-RUN] Would create Hook Swap job for {concept_id}")
            print(f"  [DRY-RUN] Would create Actor Swap job for {concept_id}")
        else:
            created_jobs = []

            if hook_swap_on_win:
                try:
                    row1 = sp.create_job(
                        offer=offer,
                        offer_code=offer_code,
                        concept_id=concept_id,
                        angle=angle,
                        job_type="Hook Swap",
                        channel=default_channel,
                    )
                    job1_name = row1.get(f"field_{sp.F_JOB_NAME}") or f"row:{row1['id']}"
                    created_jobs.append(job1_name)
                except Exception as e:
                    print(f"  ❌ Failed to create Hook Swap job: {e}")
                    job1_name = "ERROR"

            if actor_swap_on_win:
                try:
                    row2 = sp.create_job(
                        offer=offer,
                        offer_code=offer_code,
                        concept_id=concept_id,
                        angle=angle,
                        job_type="Actor Swap",
                        channel=default_channel,
                    )
                    job2_name = row2.get(f"field_{sp.F_JOB_NAME}") or f"row:{row2['id']}"
                    created_jobs.append(job2_name)
                except Exception as e:
                    print(f"  ❌ Failed to create Actor Swap job: {e}")
                    job2_name = "ERROR"

            if notify_discord and webhook_url and created_jobs:
                _post_win_embed(
                    webhook_url=webhook_url,
                    concept_id=concept_id,
                    hook=hook,
                    offer=offer,
                    hook_rate=hook_rate,
                    ctr=ctr,
                    cpa=cpa,
                    job1_name=job1_name,
                    job2_name=job2_name,
                )

            processed.add(concept_id)
            state["processed_winners"] = list(processed)
            _save_state(state)

        new_count += 1

    if new_count == 0:
        print("\n✅ No new winners to process.")
    else:
        print(f"\n✅ Processed {new_count} new winner(s).")

    if not dry_run:
        _save_state(state)


def main():
    parser = argparse.ArgumentParser(description="AutoResearchClaw Win Automation")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no writes")
    parser.add_argument("--reset", action="store_true", help="Clear processed_winners state before running")
    args = parser.parse_args()
    process_winners(dry_run=args.dry_run, reset=args.reset)


if __name__ == "__main__":
    main()

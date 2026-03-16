#!/usr/bin/env python3
"""
creative_tracker.py — ACA Creative Testing Tracker (Baserow table 819)

Mirrors the ACA Creative Testing Tracker template in Baserow.
Push new test entries, update results, list tracker rows, and post summaries to Discord.

Table: creative_testing_tracker (id=819)
Field map:
  field_8013 = Concept ID        field_8016 = Test Status (select)
  field_8017 = Asset Type (sel)  field_8018 = Hook
  field_8019 = Concept/Ad Type   field_8020 = What Are We Testing?
  field_8021 = Hypothesis        field_8022 = Action Items / Learnings
  field_8023 = Live Date         field_8024 = Results Summary
  field_8025 = Asset URL         field_8026 = Platform
  field_8027 = Offer             field_8028 = ROAS
  field_8029 = CPA               field_8030 = CVR
  field_8031 = CTR (Link)        field_8032 = Hook Rate
  field_8033 = Hold Rate         field_8034 = Avg Watch Time
  field_8035 = CTR (All)

Test Status select IDs:
  3813=Waiting  3814=Live  3815=Winner  3816=Dead  3817=Paused

Asset Type select IDs:
  3818=Net New  3819=Fix/Iteration  3820=Hook Swap  3821=Actor Swap  3822=Scene Swap

Usage:
  python creative_tracker.py list
  python creative_tracker.py list --status live
  python creative_tracker.py list --offer "Stimulus Assistance"
  python creative_tracker.py add --concept-id c004 --hook "Are you missing free money?" --type "UGC" --testing "Question hook vs stat" --hypothesis "Question creates more curiosity"
  python creative_tracker.py update --row-id 6 --hook-rate 0.38 --ctr 0.021 --cpa 4.10 --status winner
  python creative_tracker.py digest              # Post today's digest to Discord
  python creative_tracker.py winners            # List all winners with metrics
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import requests
import yaml

# ── Config ────────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"
TABLE_ID = 819
BASEROW_URL = "https://baserow.pfsend.com"

TEST_STATUS_MAP = {
    "waiting": 3813,
    "live":    3814,
    "winner":  3815,
    "dead":    3816,
    "paused":  3817,
}
TEST_STATUS_LABEL = {v: k.title() for k, v in TEST_STATUS_MAP.items()}

ASSET_TYPE_MAP = {
    "net new":        3818,
    "fix":            3819,
    "fix/iteration":  3819,
    "hook swap":      3820,
    "actor swap":     3821,
    "scene swap":     3822,
}

# Marcel kill benchmarks
HOOK_RATE_TARGET = 0.30
CTR_TARGET = 0.01
HOLD_RATE_TARGET = 0.20
ROAS_TARGET = 2.0


def _load_config() -> dict:
    if CFG_PATH.exists():
        return yaml.safe_load(CFG_PATH.read_text()) or {}
    return {}


def _baserow_auth() -> str:
    """Get JWT token from Baserow."""
    cfg = _load_config()
    baserow_cfg = cfg.get("baserow", {})
    email = baserow_cfg.get("email") or os.getenv("BASEROW_EMAIL", "ariel@pigeonfi.com")
    password = baserow_cfg.get("password") or os.getenv("BASEROW_PASSWORD", "")
    url = baserow_cfg.get("url", BASEROW_URL)

    r = requests.post(f"{url}/api/user/token-auth/", json={"email": email, "password": password}, timeout=10)
    r.raise_for_status()
    return r.json()["token"]


def _headers() -> dict:
    return {"Authorization": f"JWT {_baserow_auth()}", "Content-Type": "application/json"}


def _get_rows(status_filter: str = None, offer_filter: str = None) -> list:
    cfg = _load_config()
    url = (cfg.get("baserow", {}).get("url", BASEROW_URL))
    h = _headers()
    params = {"size": 200}
    rows = []
    page = 1
    while True:
        params["page"] = page
        r = requests.get(f"{url}/api/database/rows/table/{TABLE_ID}/", headers=h, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        rows.extend(data.get("results", []))
        if not data.get("next"):
            break
        page += 1

    if status_filter:
        sid = TEST_STATUS_MAP.get(status_filter.lower())
        rows = [row for row in rows if (row.get("field_8016") or {}).get("id") == sid]
    if offer_filter:
        rows = [row for row in rows if offer_filter.lower() in (row.get("field_8027") or "").lower()]
    return rows


def _post_row(data: dict) -> dict:
    cfg = _load_config()
    url = cfg.get("baserow", {}).get("url", BASEROW_URL)
    h = _headers()
    r = requests.post(f"{url}/api/database/rows/table/{TABLE_ID}/", headers=h, json=data, timeout=15)
    r.raise_for_status()
    return r.json()


def _patch_row(row_id: int, data: dict) -> dict:
    cfg = _load_config()
    url = cfg.get("baserow", {}).get("url", BASEROW_URL)
    h = _headers()
    r = requests.patch(f"{url}/api/database/rows/table/{TABLE_ID}/{row_id}/", headers=h, json=data, timeout=15)
    r.raise_for_status()
    return r.json()


def _post_discord(embed: dict, webhook_url: str = None):
    cfg = _load_config()
    url = webhook_url or cfg.get("notifications", {}).get("discord_webhook") or cfg.get("discord", {}).get("webhook_url")
    if not url:
        print("[WARN] No discord webhook configured")
        return
    r = requests.post(url, json={"embeds": [embed]}, timeout=15)
    if r.status_code not in (200, 204):
        print(f"[WARN] Discord post failed: {r.status_code}")


def _score_row(row: dict) -> str:
    """Return emoji signal for a row based on metrics."""
    hook = row.get("field_8032")
    ctr = row.get("field_8031")
    roas = row.get("field_8028")
    signals = []
    if hook is not None:
        signals.append("🟢 Hook" if float(hook) >= HOOK_RATE_TARGET else "🔴 Hook")
    if ctr is not None:
        signals.append("🟢 CTR" if float(ctr) >= CTR_TARGET else "🔴 CTR")
    if roas is not None:
        signals.append("🟢 ROAS" if float(roas) >= ROAS_TARGET else "🔴 ROAS")
    return " ".join(signals) if signals else "—"


def cmd_list(args):
    rows = _get_rows(status_filter=args.status, offer_filter=args.offer)
    if not rows:
        print("No rows found.")
        return

    # Group by status
    groups = {"Live": [], "Waiting": [], "Winner": [], "Dead": [], "Paused": []}
    for row in rows:
        status_val = (row.get("field_8016") or {}).get("value", "Waiting")
        groups.setdefault(status_val.title(), []).append(row)

    for status, group_rows in groups.items():
        if not group_rows:
            continue
        print(f"\n{'='*60}")
        print(f"  {status.upper()} ({len(group_rows)})")
        print(f"{'='*60}")
        for row in group_rows:
            cid = row.get("field_8013") or "—"
            hook = (row.get("field_8018") or "—")[:50]
            offer = row.get("field_8027") or "—"
            score = _score_row(row)
            hook_rate = row.get("field_8032")
            ctr = row.get("field_8031")
            roas = row.get("field_8028")
            cpa = row.get("field_8029")
            print(f"  id:{row['id']:>3} | {cid:<6} | {offer:<25} | {hook}")
            if any(v is not None for v in [hook_rate, ctr, roas, cpa]):
                m = []
                if hook_rate is not None: m.append(f"HR:{float(hook_rate):.0%}")
                if ctr is not None: m.append(f"CTR:{float(ctr):.1%}")
                if roas is not None: m.append(f"ROAS:{float(roas):.1f}x")
                if cpa is not None: m.append(f"CPA:${float(cpa):.2f}")
                print(f"         {' | '.join(m)}  {score}")


def cmd_add(args):
    status_id = TEST_STATUS_MAP.get((args.status or "waiting").lower(), 3813)
    asset_id = ASSET_TYPE_MAP.get((args.asset_type or "net new").lower(), 3818)

    data = {
        "field_8013": args.concept_id,
        "field_8016": status_id,
        "field_8017": asset_id,
        "field_8018": args.hook or "",
        "field_8019": args.ad_type or "",
        "field_8020": args.testing or "",
        "field_8021": args.hypothesis or "",
        "field_8026": args.platform or "Meta",
        "field_8027": args.offer or "",
    }
    if args.live_date:
        data["field_8023"] = args.live_date
    if args.asset_url:
        data["field_8025"] = args.asset_url

    row = _post_row(data)
    print(f"✅ Created row id:{row['id']} — Concept ID: {args.concept_id}")
    return row


def cmd_update(args):
    data = {}
    if args.status:
        data["field_8016"] = TEST_STATUS_MAP.get(args.status.lower(), 3813)
    if args.hook_rate is not None:
        data["field_8032"] = args.hook_rate
    if args.hold_rate is not None:
        data["field_8033"] = args.hold_rate
    if args.ctr is not None:
        data["field_8031"] = args.ctr
    if args.ctr_all is not None:
        data["field_8035"] = args.ctr_all
    if args.roas is not None:
        data["field_8028"] = args.roas
    if args.cpa is not None:
        data["field_8029"] = args.cpa
    if args.cvr is not None:
        data["field_8030"] = args.cvr
    if args.watch_time is not None:
        data["field_8034"] = args.watch_time
    if args.results:
        data["field_8024"] = args.results
    if args.learnings:
        data["field_8022"] = args.learnings

    if not data:
        print("[WARN] Nothing to update — pass at least one metric flag")
        return

    row = _patch_row(args.row_id, data)
    print(f"✅ Updated row id:{args.row_id} — {', '.join(data.keys())}")
    return row


def cmd_digest(args):
    """Post today's tracker digest to Discord."""
    rows = _get_rows()
    live = [r for r in rows if (r.get("field_8016") or {}).get("id") == 3814]
    winners = [r for r in rows if (r.get("field_8016") or {}).get("id") == 3815]
    waiting = [r for r in rows if (r.get("field_8016") or {}).get("id") == 3813]

    lines = []
    if live:
        lines.append(f"**🟢 Live ({len(live)})**")
        for r in live[:5]:
            cid = r.get("field_8013") or "—"
            hook = (r.get("field_8018") or "—")[:45]
            hr = r.get("field_8032")
            ctr = r.get("field_8031")
            m = []
            if hr: m.append(f"HR:{float(hr):.0%}")
            if ctr: m.append(f"CTR:{float(ctr):.1%}")
            lines.append(f"  `{cid}` {hook} {' | '.join(m)}")

    if winners:
        lines.append(f"\n**🏆 Winners ({len(winners)})**")
        for r in winners[:5]:
            cid = r.get("field_8013") or "—"
            roas = r.get("field_8028")
            cpa = r.get("field_8029")
            m = []
            if roas: m.append(f"ROAS:{float(roas):.1f}x")
            if cpa: m.append(f"CPA:${float(cpa):.2f}")
            lines.append(f"  `{cid}` {' | '.join(m)}")

    lines.append(f"\n📋 Waiting: {len(waiting)} | 🔬 Total: {len(rows)} concepts tracked")

    embed = {
        "title": f"📊 Creative Testing Tracker — {datetime.date.today().strftime('%b %d')}",
        "description": "\n".join(lines),
        "color": 0x3498DB,
        "footer": {"text": "AutoResearchClaw · Baserow creative_testing_tracker"},
    }
    _post_discord(embed)
    print("✅ Digest posted to Discord")


def cmd_winners(args):
    rows = _get_rows(status_filter="winner")
    if not rows:
        print("No winners yet.")
        return
    print(f"\n{'='*60}")
    print(f"  WINNERS ({len(rows)})")
    print(f"{'='*60}")
    for row in rows:
        cid = row.get("field_8013") or "—"
        hook = (row.get("field_8018") or "—")[:50]
        roas = row.get("field_8028")
        cpa = row.get("field_8029")
        hr = row.get("field_8032")
        ctr = row.get("field_8031")
        print(f"  {cid} | {hook}")
        m = []
        if roas: m.append(f"ROAS:{float(roas):.1f}x")
        if cpa: m.append(f"CPA:${float(cpa):.2f}")
        if hr: m.append(f"HookRate:{float(hr):.0%}")
        if ctr: m.append(f"CTR:{float(ctr):.1%}")
        if m: print(f"       {' | '.join(m)}")
        notes = row.get("field_8022") or ""
        if notes:
            print(f"       Learnings: {notes[:120]}")


# ── Public API (import from other scripts) ────────────────────────────────────

def add_test_entry(
    concept_id: str,
    hook: str,
    ad_type: str,
    testing: str,
    hypothesis: str,
    platform: str = "Meta",
    offer: str = "",
    asset_type: str = "net new",
    status: str = "waiting",
    live_date: str = None,
    asset_url: str = None,
) -> dict:
    """Add a new row to the creative testing tracker. Returns the created row."""
    status_id = TEST_STATUS_MAP.get(status.lower(), 3813)
    asset_id = ASSET_TYPE_MAP.get(asset_type.lower(), 3818)
    data = {
        "field_8013": concept_id,
        "field_8016": status_id,
        "field_8017": asset_id,
        "field_8018": hook,
        "field_8019": ad_type,
        "field_8020": testing,
        "field_8021": hypothesis,
        "field_8026": platform,
        "field_8027": offer,
    }
    if live_date:
        data["field_8023"] = live_date
    if asset_url:
        data["field_8025"] = asset_url
    return _post_row(data)


def update_metrics(
    row_id: int,
    hook_rate: float = None,
    hold_rate: float = None,
    ctr_link: float = None,
    ctr_all: float = None,
    roas: float = None,
    cpa: float = None,
    cvr: float = None,
    watch_time: float = None,
    results: str = None,
    learnings: str = None,
    status: str = None,
) -> dict:
    """Update metrics on an existing tracker row. Returns updated row."""
    data = {}
    if status: data["field_8016"] = TEST_STATUS_MAP.get(status.lower(), 3813)
    if hook_rate is not None: data["field_8032"] = hook_rate
    if hold_rate is not None: data["field_8033"] = hold_rate
    if ctr_link is not None: data["field_8031"] = ctr_link
    if ctr_all is not None: data["field_8035"] = ctr_all
    if roas is not None: data["field_8028"] = roas
    if cpa is not None: data["field_8029"] = cpa
    if cvr is not None: data["field_8030"] = cvr
    if watch_time is not None: data["field_8034"] = watch_time
    if results: data["field_8024"] = results
    if learnings: data["field_8022"] = learnings
    return _patch_row(row_id, data)


def get_live_tests(offer: str = None) -> list:
    """Return all live test rows, optionally filtered by offer."""
    return _get_rows(status_filter="live", offer_filter=offer)


def get_winners(offer: str = None) -> list:
    """Return all winner rows."""
    return _get_rows(status_filter="winner", offer_filter=offer)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ACA Creative Testing Tracker")
    sub = parser.add_subparsers(dest="cmd")

    # list
    p_list = sub.add_parser("list", help="List tracker rows")
    p_list.add_argument("--status", help="Filter by status (waiting/live/winner/dead/paused)")
    p_list.add_argument("--offer", help="Filter by offer name")

    # add
    p_add = sub.add_parser("add", help="Add a new test entry")
    p_add.add_argument("--concept-id", required=True)
    p_add.add_argument("--hook", required=True)
    p_add.add_argument("--ad-type", default="")
    p_add.add_argument("--testing", default="")
    p_add.add_argument("--hypothesis", default="")
    p_add.add_argument("--platform", default="Meta")
    p_add.add_argument("--offer", default="")
    p_add.add_argument("--asset-type", default="net new")
    p_add.add_argument("--status", default="waiting")
    p_add.add_argument("--live-date", default=None)
    p_add.add_argument("--asset-url", default=None)

    # update
    p_upd = sub.add_parser("update", help="Update metrics on a row")
    p_upd.add_argument("--row-id", type=int, required=True)
    p_upd.add_argument("--status")
    p_upd.add_argument("--hook-rate", type=float)
    p_upd.add_argument("--hold-rate", type=float)
    p_upd.add_argument("--ctr", type=float)
    p_upd.add_argument("--ctr-all", type=float)
    p_upd.add_argument("--roas", type=float)
    p_upd.add_argument("--cpa", type=float)
    p_upd.add_argument("--cvr", type=float)
    p_upd.add_argument("--watch-time", type=float)
    p_upd.add_argument("--results", default=None)
    p_upd.add_argument("--learnings", default=None)

    # digest
    p_dig = sub.add_parser("digest", help="Post tracker digest to Discord")

    # winners
    p_win = sub.add_parser("winners", help="List all winner concepts with metrics")

    args = parser.parse_args()

    if args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "add":
        cmd_add(args)
    elif args.cmd == "update":
        cmd_update(args)
    elif args.cmd == "digest":
        cmd_digest(args)
    elif args.cmd == "winners":
        cmd_winners(args)
    else:
        parser.print_help()

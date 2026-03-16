#!/usr/bin/env python3
"""
sprint_planner.py — AutoResearchClaw Sprint System (Baserow table 820)

Full CRUD interface to the production_queue table.

Field map:
  field_8036 = Name (auto — ignore)      field_8037 = Notes (long_text)
  field_8039 = Job Name (text)           field_8040 = Offer (text)
  field_8041 = Offer Code (text)         field_8042 = Job Seq Num (number)
  field_8043 = Concept ID (text)         field_8044 = Angle (text)
  field_8045 = Direction (long_text)     field_8046 = Job Type (single_select)
  field_8047 = Status (single_select)    field_8048 = Format (single_select)
  field_8049 = 9x16 (boolean)            field_8050 = 4x5 (boolean)
  field_8051 = 1x1 (boolean)             field_8052 = 16x9 (boolean)
  field_8053 = Num Deliverables (number) field_8054 = Requester (text)
  field_8055 = Creator (text)            field_8056 = Base Job (text)
  field_8057 = Reference (url)           field_8058 = Asset URL (url)
  field_8059 = Launch Date (date)        field_8060 = Completed Date (date)
  field_8061 = Channel (single_select)   field_8062 = Win (boolean)
  field_8063 = Ad Status (single_select)

Usage:
  python sprint_planner.py list [--status planning] [--offer "name"]
  python sprint_planner.py add --offer "Stimulus Assistance" --offer-code SA --concept-id c001 --angle "Discovery Moment" --job-type "Hook Swap"
  python sprint_planner.py update --row-id 5 --status "In Progress" [--asset-url URL] [--notes "..."] [--win] [--ad-status Live]
  python sprint_planner.py kanban
  python sprint_planner.py digest
"""

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

import requests
import yaml

# ── Config ────────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"
TABLE_ID = 820
BASEROW_URL = "https://baserow.pfsend.com"
BASEROW_EMAIL = "ariel@pigeonfi.com"
BASEROW_PASSWORD = "r4].Q2DtO}I88KWNcX)h"

KANBAN_ORDER = [
    "Planning",
    "In Progress",
    "Ready for Review",
    "Notes Given",
    "Ready to Launch",
    "Launched",
    "On Hold",
    "Killed",
]

STATUS_EMOJI = {
    "Planning": "📐",
    "In Progress": "🔨",
    "Ready for Review": "👀",
    "Notes Given": "📝",
    "Ready to Launch": "🚀",
    "Launched": "✅",
    "On Hold": "⏸️",
    "Killed": "💀",
}


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


_jwt_cache = {}


def _jwt() -> str:
    now = datetime.datetime.utcnow().timestamp()
    if "token" in _jwt_cache and now - _jwt_cache["ts"] < 600:
        return _jwt_cache["token"]
    token = _get_jwt()
    _jwt_cache["token"] = token
    _jwt_cache["ts"] = now
    return token


def _headers() -> dict:
    return {"Authorization": f"JWT {_jwt()}", "Content-Type": "application/json"}


def _get_fields() -> list:
    resp = requests.get(
        f"{BASEROW_URL}/api/database/fields/table/{TABLE_ID}/",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


_fields_cache = None


def _fields() -> list:
    global _fields_cache
    if _fields_cache is None:
        _fields_cache = _get_fields()
    return _fields_cache


def _get_select_id(field_id: int, value_name: str) -> int:
    """Return the integer option ID for a single_select field value."""
    for f in _fields():
        if f["id"] == field_id:
            for opt in f.get("select_options", []):
                if opt["value"].strip().lower() == value_name.strip().lower():
                    return opt["id"]
            # Case-insensitive partial match fallback
            for opt in f.get("select_options", []):
                if value_name.strip().lower() in opt["value"].strip().lower():
                    return opt["id"]
            available = [o["value"] for o in f.get("select_options", [])]
            raise ValueError(
                f"Select option '{value_name}' not found in field {field_id}. "
                f"Available: {available}"
            )
    raise ValueError(f"Field {field_id} not found in table {TABLE_ID}")


# Field ID constants
F_NOTES       = 8037
F_JOB_NAME    = 8039
F_OFFER       = 8040
F_OFFER_CODE  = 8041
F_SEQ_NUM     = 8042
F_CONCEPT_ID  = 8043
F_ANGLE       = 8044
F_DIRECTION   = 8045
F_JOB_TYPE    = 8046
F_STATUS      = 8047
F_FORMAT      = 8048
F_V916        = 8049
F_V45         = 8050
F_V11         = 8051
F_V169        = 8052
F_NUM_DELIV   = 8053
F_REQUESTER   = 8054
F_CREATOR     = 8055
F_BASE_JOB    = 8056
F_REFERENCE   = 8057
F_ASSET_URL   = 8058
F_LAUNCH_DATE = 8059
F_COMPLETED   = 8060
F_CHANNEL     = 8061
F_WIN         = 8062
F_AD_STATUS   = 8063


# ── Public API ────────────────────────────────────────────────────────────────

def get_next_seq_num(offer_code: str) -> int:
    """Return the next job sequence number for an offer code."""
    params = {
        "search": offer_code,
        "size": 200,
        "order_by": f"-field_{F_SEQ_NUM}",
    }
    resp = requests.get(
        f"{BASEROW_URL}/api/database/rows/table/{TABLE_ID}/",
        headers=_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json().get("results", [])

    max_seq = 0
    for row in rows:
        code = (row.get(f"field_{F_OFFER_CODE}") or "").strip()
        if code.upper() == offer_code.upper():
            seq = row.get(f"field_{F_SEQ_NUM}") or 0
            try:
                seq = int(seq)
            except (TypeError, ValueError):
                seq = 0
            if seq > max_seq:
                max_seq = seq

    return max_seq + 1


def generate_job_name(offer_code: str, seq_num: int, offer: str, angle: str, job_type: str) -> str:
    """Generate canonical job name: {OfferCode}{SeqNum:04d}_{OfferSlug}-{AngleSlug}_{JobType}"""
    offer_slug = re.sub(r"[^A-Za-z0-9]", "", offer)[:10]
    angle_slug = re.sub(r"[^A-Za-z0-9]", "", angle)[:15]
    type_slug = re.sub(r"[^A-Za-z0-9]", "", job_type)
    return f"{offer_code.upper()}{seq_num:04d}_{offer_slug}-{angle_slug}_{type_slug}"


def _count_deliverables(v916: bool, v45: bool, v11: bool, v169: bool) -> int:
    """Auto-calculate deliverables: variants × 4 (4 copies per variant)."""
    count = sum([v916, v45, v11, v169])
    return count * 4


def create_job(
    offer: str,
    offer_code: str,
    concept_id: str,
    angle: str,
    job_type: str,
    format_: str = "Video",
    v916: bool = True,
    v45: bool = True,
    v11: bool = False,
    v169: bool = False,
    direction: str = "",
    base_job: str = "",
    channel: str = "Meta",
    requester: str = "",
    notes: str = "",
) -> dict:
    """Create a new job in production_queue. Returns the created row."""
    seq_num = get_next_seq_num(offer_code)
    job_name = generate_job_name(offer_code, seq_num, offer, angle, job_type)

    payload = {
        f"field_{F_JOB_NAME}":   job_name,
        f"field_{F_OFFER}":      offer,
        f"field_{F_OFFER_CODE}": offer_code.upper(),
        f"field_{F_SEQ_NUM}":    seq_num,
        f"field_{F_CONCEPT_ID}": concept_id,
        f"field_{F_ANGLE}":      angle,
        f"field_{F_DIRECTION}":  direction,
        f"field_{F_V916}":       v916,
        f"field_{F_V45}":        v45,
        f"field_{F_V11}":        v11,
        f"field_{F_V169}":       v169,
        f"field_{F_NUM_DELIV}":  _count_deliverables(v916, v45, v11, v169),
        f"field_{F_REQUESTER}":  requester,
    }

    if notes:
        payload[f"field_{F_NOTES}"] = notes

    if base_job:
        payload[f"field_{F_BASE_JOB}"] = base_job

    # Single selects — look up IDs at runtime
    try:
        payload[f"field_{F_JOB_TYPE}"] = {"id": _get_select_id(F_JOB_TYPE, job_type)}
    except ValueError:
        pass  # Skip if not found — Baserow will use default

    try:
        payload[f"field_{F_STATUS}"] = {"id": _get_select_id(F_STATUS, "Planning")}
    except ValueError:
        pass

    try:
        payload[f"field_{F_FORMAT}"] = {"id": _get_select_id(F_FORMAT, format_)}
    except ValueError:
        pass

    try:
        payload[f"field_{F_CHANNEL}"] = {"id": _get_select_id(F_CHANNEL, channel)}
    except ValueError:
        pass

    resp = requests.post(
        f"{BASEROW_URL}/api/database/rows/table/{TABLE_ID}/?user_field_names=false",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    row = resp.json()
    print(f"✅ Created job: {job_name} (row_id={row['id']})")
    return row


def update_job(row_id: int, **kwargs) -> dict:
    """Update an existing job. Accepts kwargs matching field names (status, asset_url, notes, win, ad_status)."""
    payload = {}

    if "status" in kwargs and kwargs["status"]:
        try:
            payload[f"field_{F_STATUS}"] = {"id": _get_select_id(F_STATUS, kwargs["status"])}
        except ValueError as e:
            print(f"⚠️  {e}")

    if "ad_status" in kwargs and kwargs["ad_status"]:
        try:
            payload[f"field_{F_AD_STATUS}"] = {"id": _get_select_id(F_AD_STATUS, kwargs["ad_status"])}
        except ValueError as e:
            print(f"⚠️  {e}")

    if "asset_url" in kwargs and kwargs["asset_url"]:
        payload[f"field_{F_ASSET_URL}"] = kwargs["asset_url"]

    if "notes" in kwargs and kwargs["notes"]:
        payload[f"field_{F_NOTES}"] = kwargs["notes"]

    if "win" in kwargs and kwargs["win"]:
        payload[f"field_{F_WIN}"] = True

    if "launch_date" in kwargs and kwargs["launch_date"]:
        payload[f"field_{F_LAUNCH_DATE}"] = kwargs["launch_date"]

    if "completed_date" in kwargs and kwargs["completed_date"]:
        payload[f"field_{F_COMPLETED}"] = kwargs["completed_date"]

    if "creator" in kwargs and kwargs["creator"]:
        payload[f"field_{F_CREATOR}"] = kwargs["creator"]

    if "reference" in kwargs and kwargs["reference"]:
        payload[f"field_{F_REFERENCE}"] = kwargs["reference"]

    if not payload:
        print("⚠️  Nothing to update.")
        return {}

    resp = requests.patch(
        f"{BASEROW_URL}/api/database/rows/table/{TABLE_ID}/{row_id}/?user_field_names=false",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    row = resp.json()
    print(f"✅ Updated row {row_id}")
    return row


def get_jobs(status: str = None, offer: str = None, page_size: int = 200) -> list:
    """Fetch jobs from production_queue. Optionally filter by status or offer."""
    params = {"size": page_size, "order_by": f"field_{F_SEQ_NUM}"}
    if offer:
        params["search"] = offer

    resp = requests.get(
        f"{BASEROW_URL}/api/database/rows/table/{TABLE_ID}/",
        headers=_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json().get("results", [])

    if status:
        status_lower = status.strip().lower()
        rows = [
            r for r in rows
            if (r.get(f"field_{F_STATUS}") or {}).get("value", "").lower() == status_lower
        ]

    if offer and not status:
        # Also filter by offer field (search is fuzzy)
        rows = [
            r for r in rows
            if offer.lower() in (r.get(f"field_{F_OFFER}") or "").lower()
        ]

    return rows


def mark_win(row_id: int) -> dict:
    """Mark a job as a winner."""
    return update_job(row_id, win=True, ad_status="Live", status="Launched")


# ── CLI helpers ───────────────────────────────────────────────────────────────

def _format_row(row: dict) -> str:
    job_name = row.get(f"field_{F_JOB_NAME}") or f"row:{row['id']}"
    status = (row.get(f"field_{F_STATUS}") or {}).get("value", "—")
    angle = row.get(f"field_{F_ANGLE}") or "—"
    job_type = (row.get(f"field_{F_JOB_TYPE}") or {}).get("value", "—")
    emoji = STATUS_EMOJI.get(status, "•")
    return f"  {emoji} [{row['id']}] {job_name} — {status} | {job_type} | Angle: {angle}"


def cmd_list(args):
    status_filter = args.status if hasattr(args, "status") else None
    offer_filter = args.offer if hasattr(args, "offer") else None
    rows = get_jobs(status=status_filter, offer=offer_filter)

    if not rows:
        print("No jobs found.")
        return

    print(f"\n📋 Production Queue ({len(rows)} jobs)\n")
    for row in rows:
        print(_format_row(row))
    print()


def cmd_add(args):
    create_job(
        offer=args.offer,
        offer_code=args.offer_code,
        concept_id=args.concept_id,
        angle=args.angle,
        job_type=args.job_type,
        format_=getattr(args, "format", "Video") or "Video",
        v916=getattr(args, "v916", True),
        v45=getattr(args, "v45", True),
        v11=getattr(args, "v11", False),
        v169=getattr(args, "v169", False),
        direction=getattr(args, "direction", "") or "",
        base_job=getattr(args, "base_job", "") or "",
        channel=getattr(args, "channel", "Meta") or "Meta",
    )


def cmd_update(args):
    update_job(
        row_id=args.row_id,
        status=getattr(args, "status", None),
        asset_url=getattr(args, "asset_url", None),
        notes=getattr(args, "notes", None),
        win=getattr(args, "win", False),
        ad_status=getattr(args, "ad_status", None),
    )


def cmd_kanban(args):
    rows = get_jobs()
    if not rows:
        print("No jobs in queue.")
        return

    from collections import defaultdict
    groups = defaultdict(list)
    for row in rows:
        status = (row.get(f"field_{F_STATUS}") or {}).get("value", "Planning")
        groups[status].append(row)

    print("\n🏃 Sprint Kanban\n" + "=" * 60)
    for status in KANBAN_ORDER:
        group = groups.get(status, [])
        if not group:
            continue
        emoji = STATUS_EMOJI.get(status, "•")
        print(f"\n{emoji} {status.upper()} ({len(group)})")
        print("-" * 40)
        for row in group:
            job_name = row.get(f"field_{F_JOB_NAME}") or f"row:{row['id']}"
            angle = row.get(f"field_{F_ANGLE}") or "—"
            job_type = (row.get(f"field_{F_JOB_TYPE}") or {}).get("value", "—")
            print(f"  [{row['id']}] {job_name}")
            print(f"        Angle: {angle} | Type: {job_type}")
    print()


def cmd_digest(args):
    rows = get_jobs()
    if not rows:
        print("No jobs in queue.")
        return

    from collections import defaultdict
    groups = defaultdict(list)
    for row in rows:
        status = (row.get(f"field_{F_STATUS}") or {}).get("value", "Planning")
        groups[status].append(row)

    lines = ["**📋 Sprint Queue Digest**"]
    total = len(rows)
    for status in KANBAN_ORDER:
        group = groups.get(status, [])
        if not group:
            continue
        emoji = STATUS_EMOJI.get(status, "•")
        lines.append(f"\n{emoji} **{status}** ({len(group)})")
        for row in group[:5]:
            job_name = row.get(f"field_{F_JOB_NAME}") or f"row:{row['id']}"
            lines.append(f"  • {job_name}")
        if len(group) > 5:
            lines.append(f"  …and {len(group) - 5} more")

    lines.append(f"\n_Total: {total} jobs_")
    digest_text = "\n".join(lines)

    # Post to Discord webhook
    cfg = _load_config()
    webhook_url = (cfg.get("notifications") or {}).get("discord_webhook") or \
                  (cfg.get("discord") or {}).get("webhook_url")

    if webhook_url:
        requests.post(webhook_url, json={"content": digest_text}, timeout=30)
        print("✅ Digest posted to Discord.")
    else:
        print(digest_text)


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AutoResearchClaw Sprint Planner")
    sub = parser.add_subparsers(dest="cmd")

    # list
    p_list = sub.add_parser("list", help="List jobs")
    p_list.add_argument("--status", default=None, help="Filter by status")
    p_list.add_argument("--offer", default=None, help="Filter by offer name")

    # add
    p_add = sub.add_parser("add", help="Add a new job")
    p_add.add_argument("--offer", required=True)
    p_add.add_argument("--offer-code", required=True)
    p_add.add_argument("--concept-id", required=True)
    p_add.add_argument("--angle", required=True)
    p_add.add_argument("--job-type", required=True)
    p_add.add_argument("--format", default="Video")
    p_add.add_argument("--v916", action="store_true", default=True)
    p_add.add_argument("--v45", action="store_true", default=True)
    p_add.add_argument("--v11", action="store_true", default=False)
    p_add.add_argument("--v169", action="store_true", default=False)
    p_add.add_argument("--direction", default="")
    p_add.add_argument("--base-job", default="")
    p_add.add_argument("--channel", default="Meta")

    # update
    p_update = sub.add_parser("update", help="Update a job")
    p_update.add_argument("--row-id", type=int, required=True)
    p_update.add_argument("--status", default=None)
    p_update.add_argument("--asset-url", default=None)
    p_update.add_argument("--notes", default=None)
    p_update.add_argument("--win", action="store_true", default=False)
    p_update.add_argument("--ad-status", default=None)

    # kanban
    sub.add_parser("kanban", help="Show kanban board")

    # digest
    sub.add_parser("digest", help="Post queue digest to Discord")

    args = parser.parse_args()

    if args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "add":
        cmd_add(args)
    elif args.cmd == "update":
        cmd_update(args)
    elif args.cmd == "kanban":
        cmd_kanban(args)
    elif args.cmd == "digest":
        cmd_digest(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

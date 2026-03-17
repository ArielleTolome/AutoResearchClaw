from __future__ import annotations
"""
baserow_sink.py — Write AutoResearchClaw outputs to Baserow.

Handles authentication, row creation, and graceful degradation.
All writes are best-effort — failures log a warning and never crash the pipeline.

Table IDs (AutoResearchClaw database, id=214):
  intel_ads:       813
  hooks:           814
  creative_briefs: 815
  loop_results:    816
"""

import requests
from datetime import datetime, timezone

_BASE = "https://baserow.pfsend.com"

TABLE_IDS = {
    "intel_ads": 813,
    "hooks": 814,
    "creative_briefs": 815,
    "loop_results": 816,
}


def _get_jwt(config: dict) -> str | None:
    """Authenticate and return a JWT token."""
    br = config.get("baserow", {})
    email = br.get("email", "")
    password = br.get("password", "")
    if not email or not password:
        return None
    try:
        r = requests.post(
            f"{_BASE}/api/user/token-auth/",
            json={"email": email, "password": password},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()["token"]
        print(f"[BASEROW] Auth failed ({r.status_code}): {r.text[:80]}")
    except Exception as e:
        print(f"[BASEROW] Auth error: {e}")
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _write_row(jwt: str, table_id: int, data: dict) -> bool:
    try:
        r = requests.post(
            f"{_BASE}/api/database/rows/table/{table_id}/?user_field_names=true",
            headers={
                "Authorization": f"JWT {jwt}",
                "Content-Type": "application/json",
            },
            json=data,
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[BASEROW] Write error: {e}")
        return False


def write_intel_ads(config: dict, ads: list[dict], topic: str, platform: str) -> int:
    """
    Write intel ads to the intel_ads table.
    ads: list of dicts from anstrex_source / fb_ads_library_source / etc.
    Returns number of rows successfully written.
    """
    jwt = _get_jwt(config)
    if not jwt:
        print("[BASEROW] Skipping intel_ads write — no credentials")
        return 0

    count = 0
    run_date = _now_iso()

    for ad in ads:
        row = {
            "title": str(ad.get("title", ""))[:255],
            "sub_text": str(ad.get("sub_text", ad.get("description", "")))[:255],
            "source": str(ad.get("source", "unknown"))[:100],
            "platform": platform,
            "topic": topic,
            "days_running": int(ad.get("days_running", 0)),
            "ad_networks": ", ".join(ad.get("ad_networks", [])) if isinstance(ad.get("ad_networks"), list) else str(ad.get("ad_networks", "")),
            "geos": ", ".join(ad.get("geos", [])[:8]) if isinstance(ad.get("geos"), list) else str(ad.get("geos", "")),
            "ad_strength": int(ad.get("ad_strength", 0)),
            "landing_url": str(ad.get("landing_url", ad.get("url", "")))[:500],
            "hook_type": str(ad.get("hook_type", ""))[:100],
            "angle": str(ad.get("angle", ""))[:100],
            "run_date": run_date,
        }
        if _write_row(jwt, TABLE_IDS["intel_ads"], row):
            count += 1

    print(f"[BASEROW] Wrote {count}/{len(ads)} intel_ads rows")
    return count


def write_hooks(config: dict, hooks: list[dict], topic: str, platform: str) -> int:
    """
    Write extracted hooks to the hooks table.
    hooks: list of dicts with hook_text, source, hook_type, score, days_running
    """
    jwt = _get_jwt(config)
    if not jwt:
        print("[BASEROW] Skipping hooks write — no credentials")
        return 0

    count = 0
    run_date = _now_iso()

    for hook in hooks:
        row = {
            "hook_text": str(hook.get("hook_text", hook.get("text", "")))[:2000],
            "source": str(hook.get("source", ""))[:100],
            "topic": topic,
            "platform": platform,
            "hook_type": str(hook.get("hook_type", ""))[:100],
            "score": float(hook.get("score", 0)),
            "days_running": int(hook.get("days_running", 0)),
            "run_date": run_date,
        }
        if _write_row(jwt, TABLE_IDS["hooks"], row):
            count += 1

    print(f"[BASEROW] Wrote {count}/{len(hooks)} hooks rows")
    return count


def write_creative_brief(
    config: dict,
    offer: str,
    platform: str,
    topic: str,
    brief_md: str,
    status: str = "pending",
) -> int | None:
    """
    Write a generated creative brief to the creative_briefs table.
    Returns the Baserow row ID or None on failure.
    """
    jwt = _get_jwt(config)
    if not jwt:
        print("[BASEROW] Skipping creative_brief write — no credentials")
        return None

    row = {
        "offer": offer[:255],
        "platform": platform,
        "topic": topic,
        "brief_md": brief_md,
        "status": status,
        "run_date": _now_iso(),
    }
    try:
        r = requests.post(
            f"{_BASE}/api/database/rows/table/{TABLE_IDS['creative_briefs']}/?user_field_names=true",
            headers={"Authorization": f"JWT {jwt}", "Content-Type": "application/json"},
            json=row,
            timeout=10,
        )
        if r.status_code == 200:
            row_id = r.json().get("id")
            print(f"[BASEROW] Creative brief written (row_id={row_id})")
            return row_id
    except Exception as e:
        print(f"[BASEROW] Brief write error: {e}")
    return None


def update_brief_status(config: dict, row_id: int, status: str, approved_by: str = "") -> bool:
    """Update the status of an existing creative brief row."""
    jwt = _get_jwt(config)
    if not jwt:
        return False
    try:
        patch = {"status": status}
        if approved_by:
            patch["approved_by"] = approved_by
        if status == "deployed":
            patch["deployed_at"] = _now_iso()
        r = requests.patch(
            f"{_BASE}/api/database/rows/table/{TABLE_IDS['creative_briefs']}/{row_id}/?user_field_names=true",
            headers={"Authorization": f"JWT {jwt}", "Content-Type": "application/json"},
            json=patch,
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[BASEROW] Status update error: {e}")
        return False


def write_loop_result(
    config: dict,
    cycle_id: str,
    topic: str,
    platform: str,
    winner_title: str,
    challenger_copy: str,
    cpa_before: float,
    cpa_after: float,
    deployed: bool,
    learnings_summary: str,
) -> int | None:
    """Write a completed loop cycle result to loop_results table."""
    jwt = _get_jwt(config)
    if not jwt:
        print("[BASEROW] Skipping loop_result write — no credentials")
        return None

    cpa_delta = round(((cpa_after - cpa_before) / cpa_before * 100), 1) if cpa_before else 0.0

    row = {
        "cycle_id": cycle_id,
        "topic": topic,
        "platform": platform,
        "winner_title": winner_title[:255],
        "challenger_copy": challenger_copy,
        "cpa_before": round(cpa_before, 2),
        "cpa_after": round(cpa_after, 2),
        "cpa_delta_pct": cpa_delta,
        "deployed": deployed,
        "learnings_summary": learnings_summary,
        "run_date": _now_iso(),
    }
    try:
        r = requests.post(
            f"{_BASE}/api/database/rows/table/{TABLE_IDS['loop_results']}/?user_field_names=true",
            headers={"Authorization": f"JWT {jwt}", "Content-Type": "application/json"},
            json=row,
            timeout=10,
        )
        if r.status_code == 200:
            row_id = r.json().get("id")
            print(f"[BASEROW] Loop result written (row_id={row_id}, cpa_delta={cpa_delta}%)")
            return row_id
    except Exception as e:
        print(f"[BASEROW] Loop result write error: {e}")
    return None

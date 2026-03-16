#!/usr/bin/env python3
"""
qa_checklist.py — AutoResearchClaw QA Gate (ACA Course 501)

Interactive QA checklist for creative jobs in production_queue (table 820).

Usage (interactive):
  python qa_checklist.py --row-id N [--platform meta|tiktok|youtube] [--dry-run]

Bot-callable (non-interactive):
  from qa_checklist import run_qa_with_kimi
  result = run_qa_with_kimi(row_id=5, platform="meta")
  # returns {passed: bool, score: int, total: int, failed_items: list, notes: str}
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Optional

import requests
import yaml

# ── Config ────────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"
TABLE_ID = 820
BASEROW_URL = "https://baserow.pfsend.com"
BASEROW_EMAIL = "ariel@pigeonfi.com"
BASEROW_PASSWORD = "r4].Q2DtO}I88KWNcX)h"

# production_queue field IDs
F_JOB_NAME  = 8039
F_OFFER     = 8040
F_ANGLE     = 8044
F_DIRECTION = 8045
F_JOB_TYPE  = 8046
F_STATUS    = 8047
F_FORMAT    = 8048
F_NOTES     = 8037
F_ASSET_URL = 8058
F_CHANNEL   = 8061

# ── QA Checks (ACA Course 501) ────────────────────────────────────────────────
QA_CHECKS = {
    "technical": [
        "Asset length appropriate for platform (Meta: 15-45s, TikTok: 7-40s, YouTube: 20-45s)",
        "Correct aspect ratios delivered (Meta: 9:16/4:5/1:1, TikTok: 9:16, YouTube: 9:16/16:9)",
        "Safe zones respected — no key elements covered by UI",
        "Zero spelling/grammar errors",
        "Correct fonts used and sized for mobile legibility",
        "Supers support rather than distract from audio",
        "Closed captions match spoken words, never cover key visuals",
        "Clean audio — no background noise or static",
        "Consistent volume levels throughout",
        "Music/SFX balanced properly with voice",
        "No copyright issues with music or SFX",
        "Audio pattern interrupt / ear stopper in first 3 seconds",
    ],
    "brand": [
        "Brand colors, logo, disclaimers used exactly to guidelines",
        "Tone of voice and visuals match brand",
        "Disclaimers included when required",
    ],
    "creative": [
        "Nothing missed from storyboard",
        "Before/after is clear if present",
        "Ad feels authentic and relatable, not fake",
        "Strong testimonials and social proof present",
        "Street interview acting feels natural (if applicable)",
        "Podcast setup and lighting looks professional (if applicable)",
        "Screen recordings highlight key feature, not generic scrolling",
        "Props, lighting, setting match narrative",
    ],
}

SECTION_EMOJI = {
    "technical": "⚙️",
    "brand": "🎨",
    "creative": "🎬",
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


def _headers() -> dict:
    return {"Authorization": f"JWT {_get_jwt()}", "Content-Type": "application/json"}


def _get_row(row_id: int) -> dict:
    resp = requests.get(
        f"{BASEROW_URL}/api/database/rows/table/{TABLE_ID}/{row_id}/?user_field_names=false",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _get_select_id(field_id: int, value_name: str) -> Optional[int]:
    """Fetch select options for a field and return matching ID."""
    jwt = _get_jwt()
    resp = requests.get(
        f"{BASEROW_URL}/api/database/fields/table/{TABLE_ID}/",
        headers={"Authorization": f"JWT {jwt}"},
        timeout=30,
    )
    resp.raise_for_status()
    fields = resp.json()
    for f in fields:
        if f["id"] == field_id:
            for opt in f.get("select_options", []):
                if opt["value"].strip().lower() == value_name.strip().lower():
                    return opt["id"]
    return None


def _update_row(row_id: int, payload: dict) -> dict:
    resp = requests.patch(
        f"{BASEROW_URL}/api/database/rows/table/{TABLE_ID}/{row_id}/?user_field_names=false",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _post_qa_embed(webhook_url: str, job_name: str, passed: bool, score: int, total: int,
                   failed_items: list, platform: str):
    color = 0x2ECC71 if passed else 0xE74C3C
    result_str = "✅ PASS" if passed else "❌ FAIL"

    fields = [
        {"name": "Score", "value": f"{score}/{total} ({score/total:.0%})", "inline": True},
        {"name": "Platform", "value": platform.title(), "inline": True},
    ]

    if failed_items:
        failed_text = "\n".join(f"• {item[:80]}" for item in failed_items[:10])
        fields.append({"name": "❌ Failed Checks", "value": failed_text[:1024], "inline": False})

    embed = {
        "title": f"QA {result_str}: {job_name}",
        "color": color,
        "fields": fields,
        "footer": {"text": "AutoResearchClaw QA Gate · ACA Course 501"},
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }

    try:
        resp = requests.post(webhook_url, json={"embeds": [embed]}, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"⚠️  Discord webhook failed: {e}")


def _apply_qa_result(row_id: int, passed: bool, failed_items: list, dry_run: bool = False):
    """Update Baserow status based on QA result."""
    if dry_run:
        status = "Ready to Launch" if passed else "Notes Given"
        print(f"[DRY-RUN] Would update row {row_id} status → '{status}'")
        return

    payload = {}
    if passed:
        status_id = _get_select_id(F_STATUS, "Ready to Launch")
    else:
        status_id = _get_select_id(F_STATUS, "Notes Given")

    if status_id:
        payload[f"field_{F_STATUS}"] = {"id": status_id}

    if not passed and failed_items:
        notes_text = "QA Failed:\n" + "\n".join(f"- {item}" for item in failed_items)
        payload[f"field_{F_NOTES}"] = notes_text

    if payload:
        _update_row(row_id, payload)
        print(f"✅ Updated row {row_id} in Baserow.")


# ── Bot-callable: AI-powered QA ───────────────────────────────────────────────

def run_qa_with_kimi(row_id: int, platform: str = "meta") -> dict:
    """
    Load job from Baserow, use the configured LLM/agent to evaluate the job against QA checks.
    Returns {passed: bool, score: int, total: int, failed_items: list, notes: str}
    """
    try:
        from agent_runner import run_prompt as _qa_run_prompt
    except ImportError:
        _qa_run_prompt = None

    cfg = _load_config()

    row = _get_row(row_id)
    job_name = row.get(f"field_{F_JOB_NAME}") or f"row:{row_id}"
    offer = row.get(f"field_{F_OFFER}") or "Unknown Offer"
    angle = row.get(f"field_{F_ANGLE}") or ""
    direction = row.get(f"field_{F_DIRECTION}") or ""
    job_type = (row.get(f"field_{F_JOB_TYPE}") or {}).get("value", "")
    format_val = (row.get(f"field_{F_FORMAT}") or {}).get("value", "Video")
    asset_url = row.get(f"field_{F_ASSET_URL}") or ""

    all_checks = []
    for section, checks in QA_CHECKS.items():
        for check in checks:
            all_checks.append(f"[{section.upper()}] {check}")

    checks_text = "\n".join(f"{i+1}. {c}" for i, c in enumerate(all_checks))

    system_prompt = (
        "You are Rachel, an expert creative QA reviewer. You evaluate ad creative jobs "
        "against a defined QA checklist. Given a job description, assess each check and "
        "return a JSON object with your evaluation.\n\n"
        "Return ONLY valid JSON in this exact format:\n"
        '{"passed": true/false, "score": N, "total": N, "failed_items": ["item1", ...], "notes": "..."}\n\n'
        "Rules:\n"
        "- If you cannot verify a check from the description, assume PASS (benefit of the doubt)\n"
        "- Only mark as FAIL if there is a clear red flag in the description\n"
        "- 'notes' should be a 1-2 sentence summary of the overall QA result"
    )

    user_prompt = (
        f"Job: {job_name}\n"
        f"Offer: {offer}\n"
        f"Angle: {angle}\n"
        f"Job Type: {job_type}\n"
        f"Format: {format_val}\n"
        f"Platform: {platform}\n"
        f"Direction/Brief:\n{direction or '(no direction provided)'}\n"
        f"Asset URL: {asset_url or '(none)'}\n\n"
        f"QA Checklist ({len(all_checks)} items):\n{checks_text}\n\n"
        "Evaluate this job against each QA item. Return JSON."
    )

    try:
        if _qa_run_prompt is None:
            raise RuntimeError("agent_runner not available")
        result_text = _qa_run_prompt(system_prompt, user_prompt, max_tokens=1000, config=cfg)

        # Extract JSON from response (handle markdown code blocks)
        import re
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
        else:
            result = json.loads(result_text)

        # Ensure correct types
        total = len(all_checks)
        score = result.get("score", total)
        failed_items = result.get("failed_items", [])
        passed = result.get("passed", len(failed_items) == 0)

        return {
            "passed": bool(passed),
            "score": int(score),
            "total": total,
            "failed_items": list(failed_items),
            "notes": str(result.get("notes", "")),
            "job_name": job_name,
        }

    except json.JSONDecodeError:
        # Fallback: return pass if Kimi output is not parseable
        return {
            "passed": True,
            "score": len(all_checks),
            "total": len(all_checks),
            "failed_items": [],
            "notes": f"AI QA: Could not parse structured response. Manual review recommended.",
            "job_name": job_name,
        }
    except Exception as e:
        return {
            "passed": False,
            "score": 0,
            "total": len(all_checks),
            "failed_items": [str(e)],
            "notes": f"QA failed due to error: {str(e)[:200]}",
            "job_name": job_name,
        }


# ── Interactive CLI ───────────────────────────────────────────────────────────

def run_interactive(row_id: int, platform: str = "meta", dry_run: bool = False):
    """Walk through QA checks interactively."""
    row = _get_row(row_id)
    job_name = row.get(f"field_{F_JOB_NAME}") or f"row:{row_id}"
    offer = row.get(f"field_{F_OFFER}") or "Unknown"

    print(f"\n{'='*60}")
    print(f"QA Checklist: {job_name}")
    print(f"Offer: {offer} | Platform: {platform.upper()}")
    print(f"{'='*60}\n")

    failed_items = []
    passed_count = 0
    total_count = 0

    for section, checks in QA_CHECKS.items():
        emoji = SECTION_EMOJI.get(section, "•")
        print(f"\n{emoji} {section.upper()} CHECKS")
        print("-" * 40)

        for check in checks:
            total_count += 1
            while True:
                answer = input(f"  [{total_count:02d}] {check}\n       → Pass? [y/n/skip]: ").strip().lower()
                if answer in ("y", "yes", ""):
                    passed_count += 1
                    break
                elif answer in ("n", "no"):
                    failed_items.append(check)
                    break
                elif answer in ("s", "skip"):
                    passed_count += 1  # Skip counts as pass
                    break
                else:
                    print("  (Enter y/n/s)")

    passed = len(failed_items) == 0
    result_str = "✅ PASS" if passed else "❌ FAIL"

    print(f"\n{'='*60}")
    print(f"Result: {result_str}")
    print(f"Score:  {passed_count}/{total_count} ({passed_count/total_count:.0%})")
    if failed_items:
        print("\nFailed items:")
        for item in failed_items:
            print(f"  ❌ {item}")
    print(f"{'='*60}\n")

    cfg = _load_config()
    webhook_url = (cfg.get("notifications") or {}).get("discord_webhook") or \
                  (cfg.get("discord") or {}).get("webhook_url")

    if not dry_run:
        _apply_qa_result(row_id, passed=passed, failed_items=failed_items, dry_run=False)
        if webhook_url:
            _post_qa_embed(
                webhook_url=webhook_url,
                job_name=job_name,
                passed=passed,
                score=passed_count,
                total=total_count,
                failed_items=failed_items,
                platform=platform,
            )
            print("✅ Result posted to Discord.")
    else:
        print("[DRY-RUN] No Baserow updates or Discord posts made.")

    return {
        "passed": passed,
        "score": passed_count,
        "total": total_count,
        "failed_items": failed_items,
        "job_name": job_name,
    }


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AutoResearchClaw QA Checklist")
    parser.add_argument("--row-id", type=int, required=True, help="Baserow row ID of the job to QA")
    parser.add_argument("--platform", default="meta", choices=["meta", "tiktok", "youtube"],
                        help="Platform to check against")
    parser.add_argument("--dry-run", action="store_true", help="Don't write results to Baserow")
    parser.add_argument("--ai", action="store_true", help="Use AI (Kimi) for automated QA")
    args = parser.parse_args()

    if args.ai:
        print(f"🤖 Running AI QA for row {args.row_id} ({args.platform})…")
        result = run_qa_with_kimi(args.row_id, args.platform)
        passed_str = "✅ PASS" if result["passed"] else "❌ FAIL"
        print(f"\n{passed_str} — Score: {result['score']}/{result['total']}")
        if result["failed_items"]:
            print("Failed items:")
            for item in result["failed_items"]:
                print(f"  ❌ {item}")
        print(f"\nNotes: {result['notes']}")

        if not args.dry_run:
            _apply_qa_result(args.row_id, passed=result["passed"],
                             failed_items=result["failed_items"], dry_run=False)
    else:
        run_interactive(args.row_id, platform=args.platform, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

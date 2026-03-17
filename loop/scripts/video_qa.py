#!/usr/bin/env python3
from __future__ import annotations
"""
video_qa.py — Gemini QA Gate for Video Creatives

Pre-launch QA of a video creative using Gemini's video understanding.
Returns structured pass/fail with timestamps of issues.

Usage:
  video_qa.py --video /path/to.mp4 [--platform meta] [--job-name SA0001_...] [--post-discord] [--update-baserow]
  video_qa.py --url https://youtube.com/... --platform tiktok --post-discord
"""

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import requests
import yaml

# ── yt-dlp import (optional) ──────────────────────────────────────────────────
try:
    import yt_dlp  # noqa: F401
    HAS_YTDLP = True
except ImportError:
    HAS_YTDLP = False

log = logging.getLogger("video_qa")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── Config ────────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"

def _load_config() -> dict:
    if CFG_PATH.exists():
        return yaml.safe_load(CFG_PATH.read_text()) or {}
    return {}

CFG = _load_config()
BASEROW_URL = "https://baserow.pfsend.com"
BASEROW_TOKEN = "f3tuAmChdNXWaXiVgdWJySdqYWqlYVqz"
PRODUCTION_QUEUE_TABLE = 820
DISCORD_WEBHOOK = CFG.get("notifications", {}).get("discord_webhook", "") or \
                  CFG.get("discord", {}).get("webhook_url", "")
GEMINI_BIN = CFG.get("gemini", {}).get("binary", "gemini")
VIDEO_DOWNLOAD_DIR = CFG.get("gemini", {}).get("video_download_dir", "/tmp")
MAX_VIDEO_SIZE_MB = CFG.get("gemini", {}).get("max_video_size_mb", 100)

# ── Platform specs ────────────────────────────────────────────────────────────
PLATFORM_SPECS = {
    "meta":    {"max_s": 45, "min_s": 15, "ratios": ["9:16", "4:5", "1:1"], "name": "Meta"},
    "tiktok":  {"max_s": 40, "min_s": 7,  "ratios": ["9:16"],               "name": "TikTok"},
    "youtube": {"max_s": 45, "min_s": 20, "ratios": ["9:16", "16:9"],       "name": "YouTube"},
}


def _qa_prompt(platform: str) -> str:
    spec = PLATFORM_SPECS.get(platform, PLATFORM_SPECS["meta"])
    return f"""You are a creative QA specialist. Watch this video ad and evaluate it against these checklist items.

Platform: {spec["name"]} (spec: {spec["min_s"]}-{spec["max_s"]} seconds, ratios: {spec["ratios"]})

Return JSON with this exact structure:
{{
  "duration_seconds": 32,
  "detected_ratio": "9:16",
  "technical": {{
    "duration_in_spec": true,
    "ratio_correct": true,
    "audio_quality": "clean",
    "captions_present": true,
    "captions_readable": true,
    "spelling_errors_detected": false,
    "audio_pattern_interrupt_present": true,
    "audio_pattern_interrupt_timestamp": "0:02",
    "music_balanced": true
  }},
  "creative": {{
    "hook_lands_in_3s": true,
    "before_after_clear": true,
    "cta_present": true,
    "cta_timestamp": "0:28",
    "authenticity_score": 8,
    "social_proof_present": false
  }},
  "issues": [
    {{"timestamp": "0:05", "severity": "high", "description": "Caption covers speaker's face"}}
  ],
  "overall_pass": true,
  "qa_score": 87,
  "recommendation": "Ready to Launch"
}}

Return ONLY the JSON object."""


def download_video(url: str) -> str:
    """Download a YouTube (or other) URL to /tmp using yt-dlp."""
    if not HAS_YTDLP:
        print("❌ yt-dlp is not installed. Install it with: pip install yt-dlp")
        sys.exit(1)

    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    out_path = os.path.join(VIDEO_DOWNLOAD_DIR, f"arc_video_{url_hash}.mp4")

    if os.path.exists(out_path):
        log.info(f"[CACHE] Using cached video: {out_path}")
        return out_path

    log.info(f"[DOWNLOAD] Fetching: {url}")
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", out_path,
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr[:400]}")
    if not os.path.exists(out_path):
        raise FileNotFoundError(f"yt-dlp did not produce output at {out_path}")

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    if size_mb > MAX_VIDEO_SIZE_MB:
        os.remove(out_path)
        raise ValueError(f"Video too large ({size_mb:.1f}MB > {MAX_VIDEO_SIZE_MB}MB limit)")

    log.info(f"[DOWNLOAD] Saved to {out_path} ({size_mb:.1f}MB)")
    return out_path


def run_gemini_qa(video_path: str, platform: str) -> dict:
    """Run Gemini CLI QA on the video and parse JSON result."""
    prompt = f"{_qa_prompt(platform)}\n\nVideo file to analyze: {video_path}"
    log.info(f"[GEMINI] Running QA on: {video_path} (platform={platform})")

    try:
        result = subprocess.run(
            [GEMINI_BIN, "-p", prompt, "-o", "json"],
            capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        log.warning("[GEMINI] Timed out after 120s")
        return {"error": "Gemini timed out after 120s", "overall_pass": False, "qa_score": 0}
    except FileNotFoundError:
        log.error(f"[GEMINI] Binary not found: {GEMINI_BIN}")
        return {"error": f"Gemini binary not found: {GEMINI_BIN}", "overall_pass": False, "qa_score": 0}

    raw = (result.stdout or "").strip()
    if not raw:
        log.warning("[GEMINI] Empty response")
        return {"error": "Gemini returned empty response", "overall_pass": False, "qa_score": 0}

    # Strip possible markdown code fence
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning(f"[GEMINI] JSON parse failed: {e}")
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                pass
        return {"error": "Could not parse Gemini JSON", "raw": raw[:500], "overall_pass": False, "qa_score": 0}


def get_production_queue_row(job_name: str) -> tuple[int | None, dict]:
    """Find a row in production_queue table 820 by job name."""
    url = f"{BASEROW_URL}/api/database/rows/table/{PRODUCTION_QUEUE_TABLE}/?user_field_names=false&size=200"
    headers = {"Authorization": f"Token {BASEROW_TOKEN}"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        rows = resp.json().get("results", [])
        for row in rows:
            # field_8039 = Job Name
            if row.get("field_8039", "") == job_name:
                return row["id"], row
    except Exception as e:
        log.warning(f"[BASEROW] Failed to search rows: {e}")
    return None, {}


def get_select_option_ids(table_id: int) -> dict:
    """Fetch select option IDs for fields in a table."""
    url = f"{BASEROW_URL}/api/database/fields/table/{table_id}/"
    headers = {"Authorization": f"Token {BASEROW_TOKEN}"}
    option_map = {}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        for field in resp.json():
            if field.get("type") in ("single_select", "multiple_select"):
                fid = field["id"]
                for opt in field.get("select_options", []):
                    option_map[f"{fid}:{opt['value']}"] = opt["id"]
    except Exception as e:
        log.warning(f"[BASEROW] Could not fetch field options: {e}")
    return option_map


def update_baserow_status(job_name: str, passed: bool, issues: list, data: dict):
    """Update production_queue row status and notes based on QA result."""
    row_id, row = get_production_queue_row(job_name)
    if not row_id:
        log.warning(f"[BASEROW] Job '{job_name}' not found in table {PRODUCTION_QUEUE_TABLE}")
        return

    option_map = get_select_option_ids(PRODUCTION_QUEUE_TABLE)
    status_value = "Ready to Launch" if passed else "Notes Given"

    # field_8047 = Status (single_select)
    # Look up option id
    status_option_id = None
    for key, val in option_map.items():
        if key.endswith(f":{status_value}"):
            status_option_id = val
            break

    update_payload = {}
    if status_option_id:
        update_payload["field_8047"] = {"id": status_option_id}
    else:
        log.warning(f"[BASEROW] Could not find select option ID for '{status_value}'")

    # field_8037 = Notes — append issues
    if not passed and issues:
        existing_notes = row.get("field_8037", "") or ""
        issues_text = "\n".join(
            f"[{i.get('timestamp','?')}] {i.get('severity','').upper()}: {i.get('description','')}"
            for i in issues
        )
        new_notes = f"{existing_notes}\n\n--- QA Issues ---\n{issues_text}".strip()
        update_payload["field_8037"] = new_notes[:10000]

    if not update_payload:
        return

    url = f"{BASEROW_URL}/api/database/rows/table/{PRODUCTION_QUEUE_TABLE}/{row_id}/?user_field_names=false"
    headers = {
        "Authorization": f"Token {BASEROW_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.patch(url, json=update_payload, headers=headers, timeout=15)
        resp.raise_for_status()
        log.info(f"[BASEROW] Updated row {row_id} → {status_value}")
    except Exception as e:
        log.warning(f"[BASEROW] Update failed: {e}")


def post_discord(data: dict, platform: str, job_name: str = "", source_url: str = ""):
    """Post QA result as Discord embed."""
    if "error" in data and not data.get("overall_pass", None) is not None:
        payload = {
            "embeds": [{
                "title": "🎬 Video QA — Error",
                "description": data.get("error", "Unknown error"),
                "color": 0xE74C3C,
            }]
        }
    else:
        passed = data.get("overall_pass", False)
        qa_score = data.get("qa_score", 0)
        recommendation = data.get("recommendation", "")
        issues = data.get("issues", [])
        duration = data.get("duration_seconds", "?")
        detected_ratio = data.get("detected_ratio", "?")
        color = 0x27AE60 if passed else 0xE74C3C
        result_str = "✅ PASS" if passed else "❌ FAIL"

        spec = PLATFORM_SPECS.get(platform, PLATFORM_SPECS["meta"])

        fields = [
            {"name": "Platform", "value": spec["name"], "inline": True},
            {"name": "QA Score", "value": f"{qa_score}/100", "inline": True},
            {"name": "Duration", "value": f"{duration}s", "inline": True},
            {"name": "Ratio", "value": detected_ratio, "inline": True},
        ]

        if recommendation:
            fields.append({"name": "💬 Recommendation", "value": recommendation[:500], "inline": False})

        if issues:
            issues_text = "\n".join(
                f"[{i.get('timestamp','?')}] **{i.get('severity','?').upper()}**: {i.get('description','')}"
                for i in issues[:8]
            )
            fields.append({"name": "⚠️ Issues Found", "value": issues_text[:1024], "inline": False})

        # Technical summary
        tech = data.get("technical", {})
        tech_items = []
        if not tech.get("duration_in_spec", True):
            tech_items.append("❌ Duration out of spec")
        if not tech.get("ratio_correct", True):
            tech_items.append("❌ Aspect ratio wrong")
        if not tech.get("captions_present", True):
            tech_items.append("❌ No captions")
        if tech.get("spelling_errors_detected"):
            tech_items.append("❌ Spelling errors detected")
        if tech_items:
            fields.append({"name": "🔧 Technical Issues", "value": "\n".join(tech_items), "inline": False})

        if job_name:
            fields.append({"name": "Job Name", "value": job_name, "inline": True})

        title = f"🎬 Video QA {result_str}"
        if job_name:
            title += f": {job_name}"

        payload = {
            "embeds": [{
                "title": title,
                "description": source_url if source_url else "Video analysis",
                "color": color,
                "fields": fields,
                "footer": {"text": "AutoResearchClaw v2.5 · Gemini Video Intelligence"},
            }]
        }

    if not DISCORD_WEBHOOK:
        log.warning("[DISCORD] No webhook URL configured — skipping post")
        return

    try:
        resp = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
        resp.raise_for_status()
        log.info("[DISCORD] Posted QA embed")
    except Exception as e:
        log.warning(f"[DISCORD] Post failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Gemini QA Gate for video creatives")
    parser.add_argument("--video", help="Local video file path")
    parser.add_argument("--url", help="YouTube or other video URL")
    parser.add_argument("--platform", default="meta", choices=list(PLATFORM_SPECS.keys()), help="Target platform")
    parser.add_argument("--job-name", default="", help="Job name to update in sprint queue")
    parser.add_argument("--post-discord", action="store_true", help="Post result to Discord")
    parser.add_argument("--update-baserow", action="store_true", help="Update Baserow production queue status")
    parser.add_argument("--output", help="Write JSON result to this file path")
    args = parser.parse_args()

    if not args.video and not args.url:
        parser.error("Provide --video or --url")

    source_url = args.url or args.video or ""
    video_path = args.video

    # Download if URL provided
    if args.url and not args.video:
        try:
            video_path = download_video(args.url)
        except Exception as e:
            log.error(f"[DOWNLOAD] Failed: {e}")
            result = {"error": str(e), "overall_pass": False, "qa_score": 0}
            print(json.dumps(result, indent=2))
            if args.post_discord:
                post_discord(result, platform=args.platform, job_name=args.job_name, source_url=source_url)
            sys.exit(1)

    if not video_path or not os.path.exists(video_path):
        log.error(f"[ERROR] Video file not found: {video_path}")
        sys.exit(1)

    # Run QA
    data = run_gemini_qa(video_path, args.platform)

    # Output
    print(json.dumps(data, indent=2))

    if args.output:
        Path(args.output).write_text(json.dumps(data, indent=2))
        log.info(f"[OUTPUT] Saved to {args.output}")

    if args.update_baserow and args.job_name:
        issues = data.get("issues", [])
        passed = data.get("overall_pass", False)
        update_baserow_status(args.job_name, passed, issues, data)

    if args.post_discord:
        post_discord(data, platform=args.platform, job_name=args.job_name, source_url=source_url)

    return data


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
ad_dissect.py — Unicorn Ad Autopsy via Gemini Video Intelligence

Analyzes a video ad (local file or YouTube URL) using Gemini CLI and returns
a structured creative dissection following ACA methodology.

Usage:
  ad_dissect.py --video /path/to/ad.mp4 [--offer "Stimulus Assistance"] [--post-discord] [--save-baserow] [--output out.json]
  ad_dissect.py --url https://youtube.com/watch?v=XXX [--offer "..."] [--post-discord] [--save-baserow]
"""

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
import yaml

# ── yt-dlp import (optional) ──────────────────────────────────────────────────
try:
    import yt_dlp  # noqa: F401
    HAS_YTDLP = True
except ImportError:
    HAS_YTDLP = False

log = logging.getLogger("ad_dissect")
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
INTEL_ADS_TABLE = 813
DISCORD_WEBHOOK = CFG.get("notifications", {}).get("discord_webhook", "") or \
                  CFG.get("discord", {}).get("webhook_url", "")
GEMINI_BIN = CFG.get("gemini", {}).get("binary", "gemini")
VIDEO_DOWNLOAD_DIR = CFG.get("gemini", {}).get("video_download_dir", "/tmp")
MAX_VIDEO_SIZE_MB = CFG.get("gemini", {}).get("max_video_size_mb", 100)

# ── Gemini prompt ─────────────────────────────────────────────────────────────
GEMINI_PROMPT = """You are an expert ad creative analyst trained in Ad Creative Academy methodology and Schwartz Breakthrough Advertising doctrine.

Analyze this video ad frame by frame and transcribe the audio. Then provide a structured JSON analysis with these exact keys:

{
  "hook_transcript": "exact words in first 3 seconds",
  "hook_type": "one of: Greed/Relevancy/Emotions/Demographics/Cliff-hanger/Question/Negative/Urgency/Statement/Teaser/Podcast/Tutorial/Demonstration/Testimonial/Pattern Interrupt/Social Proof/Authority",
  "hook_pattern_interrupt": "describe the visual or audio pattern interrupt in first 3 seconds",
  "awareness_stage": "one of: Unaware/Problem Aware/Solution Aware/Product Aware/Most Aware",
  "sophistication_stage": "1-5 with brief reason",
  "angle": "primary angle being used (e.g. Discovery Moment, Social Proof, Us vs Them, etc)",
  "emotional_driver": "primary Life Force 8 driver being activated",
  "body_structure": "brief description of how the body builds: problem -> proof -> CTA",
  "cta_transcript": "exact CTA words",
  "cta_type": "one of: Direct/Soft/Urgency/Curiosity/Offer",
  "persona_on_screen": "who is in the ad: age range, gender, vibe, production level",
  "format": "one of: UGC/Studio/Animated/Static/Mixed",
  "estimated_duration_seconds": 30,
  "production_level": "one of: Raw UGC/Mid-Production/High Production/Studio",
  "predicted_hook_rate": "one of: <20%/20-30%/30-40%/>40% unicorn",
  "hook_rate_reasoning": "why you predict that hook rate",
  "strengths": ["list", "of", "3-5 strengths"],
  "weaknesses": ["list", "of", "1-3 weaknesses"],
  "counter_angle_opportunities": ["3 specific counter-angle ideas for a competing offer"],
  "overall_score": 75,
  "score_reasoning": "brief explanation of overall score out of 100"
}

Return ONLY the JSON object, no markdown, no explanation."""


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


def run_gemini_dissect(video_path: str) -> dict:
    """Run Gemini CLI analysis on the video and parse JSON result."""
    prompt = f"{GEMINI_PROMPT}\n\nVideo file to analyze: {video_path}"
    log.info(f"[GEMINI] Analyzing: {video_path}")

    try:
        result = subprocess.run(
            [GEMINI_BIN, "-p", prompt, "-o", "json"],
            capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        log.warning("[GEMINI] Timed out after 120s")
        return {"error": "Gemini timed out after 120s"}
    except FileNotFoundError:
        log.error(f"[GEMINI] Binary not found: {GEMINI_BIN}")
        return {"error": f"Gemini binary not found: {GEMINI_BIN}"}

    raw = (result.stdout or "").strip()
    if result.returncode != 0:
        log.warning(f"[GEMINI] Non-zero exit: {result.returncode}")
        log.warning(f"[GEMINI] stderr: {result.stderr[:300]}")

    if not raw:
        log.warning("[GEMINI] Empty response")
        return {"error": "Gemini returned empty response"}

    # Strip possible markdown code fence
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning(f"[GEMINI] JSON parse failed: {e}")
        # Try to extract JSON object from raw text
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                pass
        return {"error": "Could not parse Gemini JSON", "raw": raw[:500]}


def get_baserow_fields(table_id: int) -> dict:
    """Fetch field definitions from Baserow table."""
    url = f"{BASEROW_URL}/api/database/fields/table/{table_id}/"
    headers = {"Authorization": f"Token {BASEROW_TOKEN}"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return {f["name"]: f["id"] for f in resp.json()}


def save_to_baserow(data: dict, source_url: str = "") -> dict | None:
    """Write dissection result to Baserow intel_ads table 813."""
    try:
        fields = get_baserow_fields(INTEL_ADS_TABLE)
        log.info(f"[BASEROW] Available fields: {list(fields.keys())}")
    except Exception as e:
        log.warning(f"[BASEROW] Could not fetch fields: {e}")
        fields = {}

    # Build row payload — map to likely field names
    row = {}

    # Title / hook transcript
    title_field = next((f for f in fields if f.lower() in ("title", "hook_transcript", "name")), None)
    if title_field:
        row[f"field_{fields[title_field]}"] = data.get("hook_transcript", "")[:255]

    # Sub text / angle
    sub_field = next((f for f in fields if f.lower() in ("sub_text", "angle", "subtitle")), None)
    if sub_field:
        row[f"field_{fields[sub_field]}"] = data.get("angle", "")[:255]

    # URL field
    url_field = next((f for f in fields if "url" in f.lower() or "link" in f.lower()), None)
    if url_field and source_url:
        row[f"field_{url_field}"] = source_url[:500]

    # Long text / notes field — store full JSON
    notes_field = next((f for f in fields if f.lower() in ("notes", "body", "description", "long_text", "content", "analysis", "raw")), None)
    if notes_field:
        row[f"field_{fields[notes_field]}"] = json.dumps(data, indent=2)[:10000]

    if not row:
        # Fallback: just write to field_1, field_2 if we have nothing
        log.warning("[BASEROW] Could not map any fields — writing raw JSON as notes")
        row = {"field_1": data.get("hook_transcript", "Dissection"), "field_2": json.dumps(data)[:5000]}

    url = f"{BASEROW_URL}/api/database/rows/table/{INTEL_ADS_TABLE}/?user_field_names=false"
    headers = {
        "Authorization": f"Token {BASEROW_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, json=row, headers=headers, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        log.info(f"[BASEROW] Saved row id={result.get('id')}")
        return result
    except Exception as e:
        log.warning(f"[BASEROW] Write failed: {e}")
        return None


def post_discord(data: dict, source_url: str = "", offer: str = ""):
    """Post dissection result as a rich Discord embed."""
    if "error" in data:
        payload = {
            "embeds": [{
                "title": "🔬 Ad Dissection — Error",
                "description": data.get("error", "Unknown error"),
                "color": 0xE74C3C,
            }]
        }
    else:
        hook = data.get("hook_transcript", "N/A")[:200]
        hook_type = data.get("hook_type", "N/A")
        stage = data.get("awareness_stage", "N/A")
        angle = data.get("angle", "N/A")
        hook_rate = data.get("predicted_hook_rate", "N/A")
        score = data.get("overall_score", 0)
        strengths = data.get("strengths", [])
        weaknesses = data.get("weaknesses", [])
        counter_angles = data.get("counter_angle_opportunities", [])
        reasoning = data.get("score_reasoning", "")[:300]

        strengths_str = "\n".join(f"• {s}" for s in strengths[:5]) or "—"
        weaknesses_str = "\n".join(f"• {w}" for w in weaknesses[:3]) or "—"
        counter_str = "\n".join(f"• {c}" for c in counter_angles[:3]) or "—"

        fields = [
            {"name": "🎯 Hook", "value": f'"{hook}" [{hook_type}]', "inline": False},
            {"name": "📊 Stage", "value": stage, "inline": True},
            {"name": "🔀 Angle", "value": angle, "inline": True},
            {"name": "📈 Predicted Hook Rate", "value": hook_rate, "inline": True},
            {"name": "🏆 Score", "value": f"{score}/100", "inline": True},
            {"name": "✅ Strengths", "value": strengths_str, "inline": False},
            {"name": "⚠️ Weaknesses", "value": weaknesses_str, "inline": False},
            {"name": "💡 Counter-Angle Opportunities", "value": counter_str, "inline": False},
        ]
        if reasoning:
            fields.append({"name": "💭 Score Reasoning", "value": reasoning, "inline": False})
        if offer:
            fields.append({"name": "🎁 Offer Context", "value": offer, "inline": True})

        payload = {
            "embeds": [{
                "title": "🔬 Ad Dissection",
                "description": f"Source: {source_url}" if source_url else "Manual upload",
                "color": 0x9B59B6,
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
        log.info("[DISCORD] Posted embed")
    except Exception as e:
        log.warning(f"[DISCORD] Post failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Unicorn Ad Autopsy via Gemini")
    parser.add_argument("--video", help="Local video file path")
    parser.add_argument("--url", help="YouTube or other video URL")
    parser.add_argument("--offer", default="", help="Your offer name (for counter-angle context)")
    parser.add_argument("--post-discord", action="store_true", help="Post result to Discord")
    parser.add_argument("--save-baserow", action="store_true", help="Save to Baserow intel_ads table")
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
            result = {"error": str(e)}
            print(json.dumps(result, indent=2))
            if args.post_discord:
                post_discord(result, source_url=source_url, offer=args.offer)
            sys.exit(1)

    if not video_path or not os.path.exists(video_path):
        log.error(f"[ERROR] Video file not found: {video_path}")
        sys.exit(1)

    # Run Gemini analysis
    data = run_gemini_dissect(video_path)

    # Add offer context to result
    if args.offer:
        data["_offer_context"] = args.offer

    # Output
    print(json.dumps(data, indent=2))

    if args.output:
        Path(args.output).write_text(json.dumps(data, indent=2))
        log.info(f"[OUTPUT] Saved to {args.output}")

    if args.save_baserow:
        save_to_baserow(data, source_url=source_url)

    if args.post_discord:
        post_discord(data, source_url=source_url, offer=args.offer)

    return data


if __name__ == "__main__":
    main()

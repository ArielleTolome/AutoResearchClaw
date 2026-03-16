#!/usr/bin/env python3
"""
competitor_video_spy.py — Gemini Competitor Ad Analysis

Pulls competitor video ad URLs from Foreplay API, FB Ads Library, or direct URLs,
runs Gemini dissection on each, stores results in Baserow, and generates a
competitive landscape summary.

Usage:
  competitor_video_spy.py --keyword "stimulus check" [--source foreplay|fb|urls]
                          [--urls url1 url2] [--vertical "financial assistance"]
                          [--limit 5] [--post-discord] [--dry-run]
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

# ── anthropic import (for Kimi landscape summary) ─────────────────────────────
try:
    import anthropic as _ant
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

log = logging.getLogger("competitor_video_spy")
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
FOREPLAY_API_KEY = "mEdti_y0hpgqamGgSnsAso5YRyBVXLLZlfKjveFDzw6zPWoZcApIcY1jZpDpejvT8fNCOqU0209yBnVpWI04dQ"
FB_ACCESS_TOKEN = CFG.get("fb_ads_library", {}).get("access_token", "") or \
                  "EAAkLZBkudbJsBQypE8lgS2Cr5HWYacXqbpJB1ODj9MQ7xKRzJD1FXbvFGN9qBcHYTCt42ZCiTvtOpuHlZByhK6ZBUxukyzZCp4vv5AdATWqlKp07BsYyzAVj5AmifmI7Wz10sK7FfTAl8BRhpeaqzn2ODuZChwUI9ZAGRpkPuRvKbuwatpMKyF27gL9XpQ0SPTP0usuZBoTgPAudaxiv32xpTSnl1SEIQLfvxXg4J344rMIySbzGCYE3"

# ── Gemini dissect prompt (same as ad_dissect.py) ─────────────────────────────
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


# ── Source: Foreplay API ──────────────────────────────────────────────────────

def fetch_foreplay_ads(keyword: str, limit: int = 5) -> list[dict]:
    """Fetch video ads from Foreplay API."""
    url = "https://public.api.foreplay.co/ads/search"
    headers = {"Authorization": FOREPLAY_API_KEY}
    params = {"q": keyword, "limit": min(limit * 2, 20)}  # fetch extra since not all have video

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        ads = data if isinstance(data, list) else data.get("data", data.get("results", []))
    except Exception as e:
        log.warning(f"[FOREPLAY] API error: {e}")
        return []

    results = []
    for ad in ads:
        video_url = ad.get("videoUrl") or ad.get("video_url") or ""
        if not video_url:
            continue
        results.append({
            "id": ad.get("id", ""),
            "brand": ad.get("brandName", ad.get("brand_name", "")),
            "headline": ad.get("headline", ""),
            "video_url": video_url,
            "thumbnail_url": ad.get("thumbnailUrl", ad.get("thumbnail_url", "")),
            "platform": ad.get("platform", "meta"),
            "source": "foreplay",
        })
        if len(results) >= limit:
            break

    log.info(f"[FOREPLAY] Found {len(results)} video ads for '{keyword}'")
    return results


# ── Source: FB Ads Library ────────────────────────────────────────────────────

def fetch_fb_video_ads(keyword: str, limit: int = 5) -> list[dict]:
    """Fetch video ads from Facebook Ads Library."""
    url = "https://graph.facebook.com/v19.0/ads_archive"
    params = {
        "search_terms": keyword,
        "ad_reached_countries": '["US"]',
        "fields": "id,ad_creative_bodies,ad_creative_link_titles,page_name,ad_delivery_start_time,ad_snapshot_url",
        "limit": limit * 3,
        "access_token": FB_ACCESS_TOKEN,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as e:
        log.warning(f"[FB_ADS] API error: {e}")
        return []

    results = []
    for ad in data:
        snapshot_url = ad.get("ad_snapshot_url", "")
        # FB Ads Library doesn't always return direct video URLs in basic API
        # Use snapshot URL as reference and note it's not directly downloadable
        if not snapshot_url:
            continue
        bodies = ad.get("ad_creative_bodies", [])
        titles = ad.get("ad_creative_link_titles", [])
        results.append({
            "id": ad.get("id", ""),
            "brand": ad.get("page_name", ""),
            "headline": titles[0] if titles else "",
            "video_url": snapshot_url,  # snapshot URL — may not be direct download
            "thumbnail_url": "",
            "platform": "facebook",
            "source": "fb_ads_library",
            "ad_body": bodies[0] if bodies else "",
        })
        if len(results) >= limit:
            break

    log.info(f"[FB_ADS] Found {len(results)} ads for '{keyword}'")
    return results


# ── Video download ────────────────────────────────────────────────────────────

def download_video(url: str) -> str | None:
    """Download a video URL to /tmp using yt-dlp. Returns path or None on failure."""
    if not HAS_YTDLP:
        log.error("yt-dlp is not installed. Install it with: pip install yt-dlp")
        return None

    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    out_path = os.path.join(VIDEO_DOWNLOAD_DIR, f"arc_video_{url_hash}.mp4")

    if os.path.exists(out_path):
        log.info(f"[CACHE] Using cached: {out_path}")
        return out_path

    log.info(f"[DOWNLOAD] Fetching: {url}")
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", out_path,
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            log.warning(f"[DOWNLOAD] yt-dlp failed for {url}: {result.stderr[:200]}")
            return None
        if not os.path.exists(out_path):
            log.warning(f"[DOWNLOAD] No output file for {url}")
            return None
        size_mb = os.path.getsize(out_path) / (1024 * 1024)
        if size_mb > MAX_VIDEO_SIZE_MB:
            os.remove(out_path)
            log.warning(f"[DOWNLOAD] Too large ({size_mb:.1f}MB) — skipping")
            return None
        log.info(f"[DOWNLOAD] Saved {out_path} ({size_mb:.1f}MB)")
        return out_path
    except Exception as e:
        log.warning(f"[DOWNLOAD] Error: {e}")
        return None


# ── Gemini dissect ────────────────────────────────────────────────────────────

def run_gemini_dissect(video_path: str) -> dict:
    """Run Gemini CLI on a video file and return parsed dissection dict."""
    prompt = f"{GEMINI_PROMPT}\n\nVideo file to analyze: {video_path}"

    try:
        result = subprocess.run(
            [GEMINI_BIN, "-p", prompt, "-o", "json"],
            capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        log.warning("[GEMINI] Timed out")
        return {"error": "Gemini timed out"}
    except FileNotFoundError:
        return {"error": f"Gemini binary not found: {GEMINI_BIN}"}

    raw = (result.stdout or "").strip()
    if not raw:
        return {"error": "Gemini returned empty response"}

    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                pass
        return {"error": "Could not parse Gemini JSON", "raw": raw[:300]}


# ── Baserow ────────────────────────────────────────────────────────────────────

def save_to_baserow(data: dict, source_url: str = "", brand: str = "") -> dict | None:
    """Save dissection to Baserow intel_ads table."""
    url = f"{BASEROW_URL}/api/database/fields/table/{INTEL_ADS_TABLE}/"
    headers = {"Authorization": f"Token {BASEROW_TOKEN}"}
    fields = {}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        fields = {f["name"]: f["id"] for f in resp.json()}
    except Exception as e:
        log.warning(f"[BASEROW] Could not fetch fields: {e}")

    row = {}
    title_field = next((f for f in fields if f.lower() in ("title", "hook_transcript", "name")), None)
    if title_field:
        hook_txt = data.get("hook_transcript", "")
        row[f"field_{fields[title_field]}"] = f"[{brand}] {hook_txt}"[:255] if brand else hook_txt[:255]

    sub_field = next((f for f in fields if f.lower() in ("sub_text", "angle", "subtitle")), None)
    if sub_field:
        row[f"field_{fields[sub_field]}"] = data.get("angle", "")[:255]

    url_field = next((f for f in fields if "url" in f.lower() or "link" in f.lower()), None)
    if url_field and source_url:
        row[f"field_{fields[url_field]}"] = source_url[:500]

    notes_field = next((f for f in fields if f.lower() in ("notes", "body", "description", "long_text", "content", "analysis", "raw")), None)
    if notes_field:
        row[f"field_{fields[notes_field]}"] = json.dumps(data, indent=2)[:10000]

    if not row:
        row = {"field_1": f"[{brand}] " + data.get("hook_transcript", "Dissection")[:200]}

    post_url = f"{BASEROW_URL}/api/database/rows/table/{INTEL_ADS_TABLE}/?user_field_names=false"
    post_headers = {"Authorization": f"Token {BASEROW_TOKEN}", "Content-Type": "application/json"}
    try:
        resp = requests.post(post_url, json=row, headers=post_headers, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        log.info(f"[BASEROW] Saved row id={result.get('id')}")
        return result
    except Exception as e:
        log.warning(f"[BASEROW] Write failed: {e}")
        return None


# ── Discord ────────────────────────────────────────────────────────────────────

def post_ad_card(data: dict, ad_meta: dict, idx: int, total: int):
    """Post a single ad dissection card to Discord."""
    if "error" in data:
        desc = f"⚠️ Dissection failed: {data['error']}"
        color = 0x95A5A6
    else:
        hook = data.get("hook_transcript", "N/A")[:200]
        hook_type = data.get("hook_type", "N/A")
        angle = data.get("angle", "N/A")
        stage = data.get("awareness_stage", "N/A")
        score = data.get("overall_score", 0)
        hook_rate = data.get("predicted_hook_rate", "N/A")
        desc = f'🎯 Hook: "{hook}" [{hook_type}]\n📊 Stage: {stage} | 🔀 Angle: {angle}\n📈 Hook Rate: {hook_rate} | 🏆 Score: {score}/100'
        color = 0xE74C3C

    brand = ad_meta.get("brand", "Unknown")
    source = ad_meta.get("source", "?")
    video_url = ad_meta.get("video_url", "")

    payload = {
        "embeds": [{
            "title": f"🕵️ Ad {idx}/{total}: {brand}",
            "description": desc,
            "color": color,
            "fields": [
                {"name": "Source", "value": source, "inline": True},
                {"name": "Platform", "value": ad_meta.get("platform", "?"), "inline": True},
            ],
            "footer": {"text": f"AutoResearchClaw v2.5 · Competitor Spy"},
        }]
    }
    if video_url:
        payload["embeds"][0]["url"] = video_url[:500]

    if DISCORD_WEBHOOK:
        try:
            resp = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            log.warning(f"[DISCORD] Card post failed: {e}")


def post_landscape_summary(keyword: str, dissections: list[dict], brands: list[str]):
    """Generate and post competitive landscape summary using Kimi."""
    if not dissections:
        return

    # Build data for Kimi
    angles = [d.get("angle", "Unknown") for d in dissections if "error" not in d]
    hook_types = [d.get("hook_type", "Unknown") for d in dissections if "error" not in d]
    stages = [d.get("awareness_stage", "Unknown") for d in dissections if "error" not in d]
    prod_levels = [d.get("production_level", "Unknown") for d in dissections if "error" not in d]

    from collections import Counter
    angle_counts = Counter(angles)
    hook_counts = Counter(hook_types)

    data_summary = {
        "keyword": keyword,
        "ads_analyzed": len([d for d in dissections if "error" not in d]),
        "brands": brands[:5],
        "angle_breakdown": dict(angle_counts.most_common(5)),
        "hook_type_breakdown": dict(hook_counts.most_common(5)),
        "awareness_stages": list(set(stages)),
        "production_levels": list(set(prod_levels)),
    }

    system_prompt = (
        "You are an ad creative strategist analyzing competitive landscape data. "
        "Generate a concise competitive landscape report identifying dominant patterns, "
        "overused angles, and untapped opportunities. Be specific and actionable."
    )
    user_prompt = (
        f"Competitive landscape data for keyword '{keyword}':\n"
        f"{json.dumps(data_summary, indent=2)}\n\n"
        "Produce a landscape report with these sections:\n"
        "1. Dominant Angles (what's being used most)\n"
        "2. Most Common Hook Types\n"
        "3. ⚠️ OVERUSED patterns\n"
        "4. 💡 GAP: What nobody is using\n"
        "5. 🎯 OPPORTUNITY: Specific recommendation to win\n\n"
        "Keep it punchy — 10 lines max."
    )

    landscape_text = ""
    if HAS_ANTHROPIC:
        try:
            llm_cfg = CFG.get("llm") or {}
            api_key = llm_cfg.get("api_key", "")
            base_url = llm_cfg.get("base_url") or None
            model = llm_cfg.get("model", "MiniMax-M2.5-highspeed")
            if api_key:
                client_kwargs = {"api_key": api_key}
                if base_url:
                    client_kwargs["base_url"] = base_url
                client = _ant.Anthropic(**client_kwargs)
                resp = client.messages.create(
                    model=model, max_tokens=800,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}]
                )
                landscape_text = next((b.text for b in resp.content if hasattr(b, "text")), "")
        except Exception as e:
            log.warning(f"[KIMI] Landscape summary failed: {e}")

    if not landscape_text:
        # Fallback: build summary from raw data
        top_angles = ", ".join(f"{k} ({v})" for k, v in angle_counts.most_common(3))
        top_hooks = ", ".join(f"{k} ({v})" for k, v in hook_counts.most_common(3))
        landscape_text = (
            f"Dominant angles: {top_angles}\n"
            f"Top hook types: {top_hooks}\n"
            f"Awareness stages seen: {', '.join(set(stages))}\n"
            f"Production levels: {', '.join(set(prod_levels))}"
        )

    if not DISCORD_WEBHOOK:
        log.info(f"[LANDSCAPE]\n{landscape_text}")
        return

    payload = {
        "embeds": [{
            "title": f"🕵️ Competitive Landscape: \"{keyword}\"",
            "description": f"Analyzed **{data_summary['ads_analyzed']}** ads from: {', '.join(brands[:5])}\n\n{landscape_text}",
            "color": 0xE74C3C,
            "footer": {"text": "AutoResearchClaw v2.5 · Gemini Video Intelligence"},
        }]
    }
    try:
        resp = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
        resp.raise_for_status()
        log.info("[DISCORD] Posted landscape summary")
    except Exception as e:
        log.warning(f"[DISCORD] Landscape post failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Gemini Competitor Ad Analysis")
    parser.add_argument("--keyword", required=True, help="Search keyword")
    parser.add_argument("--source", default="foreplay", choices=["foreplay", "fb", "urls"],
                        help="Ad source")
    parser.add_argument("--urls", nargs="*", default=[], help="Direct video URLs (for --source urls)")
    parser.add_argument("--vertical", default="", help="Vertical description for context")
    parser.add_argument("--limit", type=int, default=5, help="Max ads to analyze (1-5)")
    parser.add_argument("--post-discord", action="store_true", help="Post results to Discord")
    parser.add_argument("--dry-run", action="store_true", help="Dry run — skip Gemini and Baserow")
    args = parser.parse_args()

    limit = min(args.limit, 5)
    log.info(f"[SPY] Starting competitor spy: keyword='{args.keyword}' source={args.source} limit={limit}")

    # Fetch ads
    if args.source == "foreplay":
        ads = fetch_foreplay_ads(args.keyword, limit=limit)
    elif args.source == "fb":
        ads = fetch_fb_video_ads(args.keyword, limit=limit)
    elif args.source == "urls":
        ads = [{"id": str(i), "brand": "Direct", "video_url": u, "platform": "unknown",
                "source": "direct", "headline": "", "thumbnail_url": ""}
               for i, u in enumerate(args.urls[:limit])]
    else:
        ads = []

    if not ads:
        log.warning("[SPY] No ads found — exiting")
        print(json.dumps({"status": "no_ads", "keyword": args.keyword}))
        return

    log.info(f"[SPY] Processing {len(ads)} ads")
    dissections = []
    brands = []

    for idx, ad in enumerate(ads, 1):
        video_url = ad.get("video_url", "")
        brand = ad.get("brand", f"Brand {idx}")
        brands.append(brand)
        log.info(f"[SPY] [{idx}/{len(ads)}] {brand}: {video_url}")

        if args.dry_run:
            data = {"angle": "Discovery Moment", "hook_type": "Question",
                    "awareness_stage": "Problem Aware", "overall_score": 72,
                    "_dry_run": True}
            dissections.append(data)
            log.info(f"[SPY] DRY RUN — skipping actual analysis")
            continue

        # Download
        video_path = None
        if video_url:
            video_path = download_video(video_url)

        if not video_path:
            log.warning(f"[SPY] Could not download {video_url} — skipping")
            dissections.append({"error": "Download failed", "brand": brand})
            continue

        # Dissect
        data = run_gemini_dissect(video_path)
        data["_brand"] = brand
        data["_source_url"] = video_url
        dissections.append(data)

        # Save to Baserow
        save_to_baserow(data, source_url=video_url, brand=brand)

        # Post individual card
        if args.post_discord:
            post_ad_card(data, ad, idx, len(ads))

    # Print summary
    output = {
        "keyword": args.keyword,
        "source": args.source,
        "ads_analyzed": len(dissections),
        "dissections": dissections,
    }
    print(json.dumps(output, indent=2))

    # Post landscape summary
    if args.post_discord:
        post_landscape_summary(args.keyword, dissections, brands)


if __name__ == "__main__":
    main()

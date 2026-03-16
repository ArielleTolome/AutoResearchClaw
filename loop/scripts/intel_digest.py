#!/usr/bin/env python3
"""
intel_digest.py — Daily morning digest for AutoResearchClaw
Reads last 24h from Baserow tables 767 (news) and 818 (reddit audience signals),
builds a summary embed and posts to #intel.

Usage:
  python intel_digest.py [--dry-run] [--date YYYY-MM-DD]
"""

import os, sys, json, argparse, datetime
from collections import Counter
from pathlib import Path

import requests
import yaml

# ── Config ───────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"

def load_config() -> dict:
    if CFG_PATH.exists():
        return yaml.safe_load(CFG_PATH.read_text()) or {}
    return {}

CFG              = load_config()
BASEROW_KEY      = CFG.get("baserow", {}).get("api_key", os.getenv("BASEROW_TOKEN", "Yg1DSyEipxerKG9cuVJg6p6OjN0RTN4R"))
BASEROW_BASE_URL = CFG.get("baserow", {}).get("url", "https://baserow.pfsend.com")
INTEL_WEBHOOK    = CFG.get("discord", {}).get("intel_webhook_url", os.getenv("INTEL_WEBHOOK_URL", ""))

NEWS_TABLE   = 767   # leadgen_industry_news (existing)
REDDIT_TABLE = 818   # reddit_audience_signals (new)

SENTIMENT_EMOJI = {
    "Very Bullish": "🚀", "Bullish": "📈", "Neutral": "➡️",
    "Bearish": "📉", "Very Bearish": "🔻",
}
EMOTION_EMOJI = {
    "Frustrated": "😤", "Confused": "😕", "Angry": "😡",
    "Relieved": "😌", "Hopeful": "🙏", "Anxious": "😰", "Neutral": "😐",
}


def fetch_baserow(table_id: int, size: int = 50) -> list[dict]:
    """Fetch latest N rows from a Baserow table."""
    url = f"{BASEROW_BASE_URL}/api/database/rows/table/{table_id}/?user_field_names=true&size={size}"
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Token {BASEROW_KEY}"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:
        print(f"  [WARN] Baserow fetch failed (table {table_id}): {e}")
        return []


def build_embed(news_rows: list[dict], reddit_rows: list[dict], date_str: str) -> dict:
    total = len(news_rows) + len(reddit_rows)

    def _str_field(val, default="Other"):
        """Unwrap Baserow select dicts or return string as-is."""
        if isinstance(val, dict):
            return val.get("value", default)
        return str(val) if val else default

    # Top 3 news signals (Baserow field names use Title Case)
    news_bullets = []
    for row in news_rows[:3]:
        vertical  = _str_field(row.get("Vertical") or row.get("vertical"), "General")
        sentiment = _str_field(row.get("Sentiment Impact") or row.get("sentiment_impact"), "Neutral")
        headline  = (row.get("Headline") or row.get("headline") or row.get("title") or "")[:80]
        emoji = SENTIMENT_EMOJI.get(sentiment, "➡️")
        news_bullets.append(f"**{vertical}** {emoji} — {headline}")

    # Top 3 audience signals
    reddit_bullets = []
    for row in reddit_rows[:3]:
        hook = (row.get("verbatim_hook") or row.get("title") or "")[:100]
        sub = row.get("subreddit", "")
        emotion = row.get("emotion", "")
        emoji = EMOTION_EMOJI.get(emotion, "")
        reddit_bullets.append(f"_{hook}_ {emoji} — r/{sub}")

    # Vertical pulse — combined count
    all_verticals = (
        [_str_field(r.get("Vertical") or r.get("vertical")) for r in news_rows] +
        [_str_field(r.get("vertical")) for r in reddit_rows]
    )
    vertical_counts = Counter(all_verticals).most_common(8)
    pulse_lines = [f"{v}: **{c}**" for v, c in vertical_counts]

    fields = []
    if news_bullets:
        fields.append({
            "name":   "🔥 Top News Signals",
            "value":  "\n".join(news_bullets) or "No news signals today",
            "inline": False,
        })
    if reddit_bullets:
        fields.append({
            "name":   "💬 Top Audience Signals",
            "value":  "\n".join(reddit_bullets) or "No audience signals today",
            "inline": False,
        })
    if pulse_lines:
        fields.append({
            "name":   "📈 Vertical Pulse",
            "value":  " · ".join(pulse_lines[:6]),
            "inline": False,
        })

    return {
        "title":       f"📊 Daily Intel Digest — {date_str}",
        "color":       0x3498DB,
        "fields":      fields,
        "footer":      {"text": f"AutoResearchClaw v1.9 · {total} signals processed"},
        "timestamp":   datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date", help="Date string for title (default: today)")
    args = parser.parse_args()

    date_str = args.date or datetime.date.today().strftime("%B %d, %Y")

    if not BASEROW_KEY:
        print("[WARN] No BASEROW_API_KEY — digest will be empty")

    print(f"[digest] Fetching signals for {date_str}...")
    news_rows   = fetch_baserow(NEWS_TABLE)
    reddit_rows = fetch_baserow(REDDIT_TABLE)
    print(f"  {len(news_rows)} news signals, {len(reddit_rows)} audience signals")

    embed = build_embed(news_rows, reddit_rows, date_str)

    if args.dry_run:
        print("\n[DRY-RUN] Would post embed:")
        print(json.dumps(embed, indent=2))
        return

    if not INTEL_WEBHOOK:
        print("[WARN] No intel_webhook_url configured — nothing to post")
        return

    try:
        r = requests.post(INTEL_WEBHOOK, json={"embeds": [embed]}, timeout=10)
        r.raise_for_status()
        print(f"✅ Digest posted to #intel ({len(news_rows) + len(reddit_rows)} signals)")
    except Exception as e:
        print(f"[ERROR] Discord post failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

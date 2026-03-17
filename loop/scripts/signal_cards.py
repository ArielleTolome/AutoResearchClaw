#!/usr/bin/env python3
from __future__ import annotations
"""
signal_cards.py — Generate button payload JSON for the OpenClaw Rachel bot.
Reads fresh signals from Baserow (last 24h, tables 767 + 818), builds
structured card dicts with action buttons, and saves to loop/output/.

NOTE: signal_cards.py generates button payloads — the actual interactive
posting is handled by the OpenClaw Rachel agent, which reads these files
and posts via Discord bot with live buttons.

Usage:
  python signal_cards.py                              # Default output
  python signal_cards.py --output cards.json          # Custom output path
  python signal_cards.py --dry-run                    # Print cards, don't save
"""

import os, sys, json, argparse, datetime, hashlib
from pathlib import Path

import requests
import yaml

# ── Config ───────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"
OUTPUT_DIR = Path(__file__).parent.parent / "output"

def _load_config() -> dict:
    if CFG_PATH.exists():
        return yaml.safe_load(CFG_PATH.read_text()) or {}
    return {}

CFG = _load_config()
BASEROW_KEY = CFG.get("baserow", {}).get("api_key", os.getenv("BASEROW_TOKEN", "Yg1DSyEipxerKG9cuVJg6p6OjN0RTN4R"))
BASEROW_BASE_URL = CFG.get("baserow", {}).get("url", "https://baserow.pfsend.com")
WEBHOOK_URL = CFG.get("discord", {}).get("webhook_url") or os.getenv("DISCORD_WEBHOOK_URL", "")

NEWS_TABLE = 767
REDDIT_TABLE = 818

EMOTION_COLOR = {
    "Frustrated": 0xE74C3C, "Angry": 0xE74C3C,
    "Hopeful": 0x2ECC71, "Relieved": 0x2ECC71,
    "Confused": 0xF39C12, "Anxious": 0xE67E22,
    "Neutral": 0x95A5A6,
}
SOURCE_EMOJI = {"News": "📰", "Reddit": "💬"}


# ── Baserow fetch ────────────────────────────────────────────────────────────

def _fetch_rows(table_id: int, size: int = 50) -> list[dict]:
    """Fetch recent rows from Baserow."""
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
        print(f"[cards] Baserow fetch failed (table {table_id}): {e}")
        return []


def _str_field(val, default=""):
    """Unwrap Baserow select dicts or return string as-is."""
    if isinstance(val, dict):
        return val.get("value", default)
    return str(val) if val else default


def _signal_id(row: dict) -> str:
    """Generate a unique signal ID from row content."""
    key = (
        str(row.get("id", "")) +
        (row.get("Headline") or row.get("title") or "") +
        (row.get("Source URL") or row.get("url") or "")
    )
    return hashlib.sha1(key.encode()).hexdigest()[:12]


# ── Card builder ─────────────────────────────────────────────────────────────

def _build_news_card(row: dict) -> dict:
    """Build a signal card from a news row (table 767)."""
    signal_id = _signal_id(row)
    return {
        "signal_id": signal_id,
        "vertical": _str_field(row.get("Vertical") or row.get("vertical"), "Unknown"),
        "headline": (row.get("Headline") or row.get("headline") or "")[:200],
        "source": "News",
        "emotion": _str_field(row.get("Sentiment Impact") or row.get("sentiment_impact"), "Neutral"),
        "summary": (row.get("Summary") or row.get("summary") or "")[:500],
        "source_url": row.get("Source URL") or row.get("source_url", ""),
        "buttons": [
            {"label": "📋 Brief", "action": "generate_brief", "signal_id": signal_id},
            {"label": "👤 Persona", "action": "build_persona", "signal_id": signal_id},
            {"label": "🎣 Hooks", "action": "generate_hooks", "signal_id": signal_id},
            {"label": "📝 Script", "action": "write_script", "signal_id": signal_id},
            {"label": "🖼️ Image Ad", "action": "image_concept", "signal_id": signal_id},
        ],
    }


def _build_reddit_card(row: dict) -> dict:
    """Build a signal card from a reddit row (table 818)."""
    signal_id = _signal_id(row)
    return {
        "signal_id": signal_id,
        "vertical": _str_field(row.get("vertical"), "Unknown"),
        "headline": (row.get("verbatim_hook") or row.get("title") or "")[:200],
        "source": "Reddit",
        "emotion": _str_field(row.get("emotion"), "Neutral"),
        "summary": (row.get("pain_point") or "")[:500],
        "source_url": row.get("url", ""),
        "buttons": [
            {"label": "📋 Brief", "action": "generate_brief", "signal_id": signal_id},
            {"label": "👤 Persona", "action": "build_persona", "signal_id": signal_id},
            {"label": "🎣 Hooks", "action": "generate_hooks", "signal_id": signal_id},
            {"label": "📝 Script", "action": "write_script", "signal_id": signal_id},
            {"label": "🖼️ Image Ad", "action": "image_concept", "signal_id": signal_id},
        ],
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def generate_cards() -> list[dict]:
    """Fetch fresh signals and build card payloads."""
    print("[cards] Fetching news signals (table 767)...")
    news_rows = _fetch_rows(NEWS_TABLE)
    print(f"  {len(news_rows)} news rows")

    print("[cards] Fetching reddit signals (table 818)...")
    reddit_rows = _fetch_rows(REDDIT_TABLE)
    print(f"  {len(reddit_rows)} reddit rows")

    cards = []
    for row in news_rows:
        cards.append(_build_news_card(row))
    for row in reddit_rows:
        cards.append(_build_reddit_card(row))

    return cards


def _post_cards_to_discord(cards: list[dict], webhook_url: str | None = None):
    """Post signal cards as Discord embeds (max 10)."""
    url = webhook_url or WEBHOOK_URL
    if not url:
        print("[cards] No discord.webhook_url configured — skipping post")
        return

    posted = 0
    for card in cards[:10]:
        emoji = SOURCE_EMOJI.get(card.get("source"), "📡")
        vertical = card.get("vertical", "Unknown")
        source = card.get("source", "")
        headline = card.get("headline", "")[:200]
        summary = card.get("summary", "")[:200]
        source_url = card.get("source_url", "")
        emotion = card.get("emotion", "Neutral")
        color = EMOTION_COLOR.get(emotion, 0x95A5A6)

        desc_parts = [headline]
        if summary:
            desc_parts.append(f"\n{summary}")
        if source_url:
            desc_parts.append(f"\n[Source]({source_url})")

        embed = {
            "title": f"{emoji} {vertical} · {source}",
            "description": "\n".join(desc_parts),
            "color": color,
            "footer": {"text": "Click to run: Brief · Persona · Hooks · Script · Image Ad | AutoResearchClaw"},
        }
        try:
            r = requests.post(url, json={"embeds": [embed]}, timeout=15)
            if r.status_code in (200, 204):
                posted += 1
            else:
                print(f"[cards] Discord error {r.status_code}: {r.text[:100]}")
        except Exception as e:
            print(f"[cards] Discord post failed: {e}")

    print(f"[cards] Posted {posted}/{min(len(cards), 10)} cards to Discord")


def main():
    parser = argparse.ArgumentParser(description="Signal card button payload generator")
    parser.add_argument("--output", help="Custom output file path")
    parser.add_argument("--dry-run", action="store_true", help="Print cards, don't save")
    parser.add_argument("--post", action="store_true", help="Post signal cards to Discord as embeds")
    args = parser.parse_args()

    cards = generate_cards()
    print(f"\n[cards] Generated {len(cards)} signal cards")

    if args.dry_run:
        print(json.dumps(cards[:5], indent=2))
        print(f"  ... ({len(cards)} total, showing first 5)")
        return

    # Determine output path
    date_str = datetime.date.today().strftime("%Y%m%d")  # always defined first
    if args.output:
        _op = Path(args.output)
        # If a directory was passed, auto-generate dated filename inside it
        if _op.is_dir() or str(args.output).endswith("/"):
            out_path = _op / f"signal_cards_{date_str}.json"
        else:
            out_path = _op
    else:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUTPUT_DIR / f"signal_cards_{date_str}.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cards, indent=2))
    print(f"[cards] Saved to {out_path}")

    if args.post:
        _post_cards_to_discord(cards)

    print(f"✅ Done — {len(cards)} signal cards written")


if __name__ == "__main__":
    main()

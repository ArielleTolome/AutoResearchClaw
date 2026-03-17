#!/usr/bin/env python3
from __future__ import annotations
"""
persona_builder.py — Auto persona builder from audience signals.
Reads Reddit signals from Qdrant (or Baserow fallback), sends to Claude
to generate a persona card, and posts to Discord.

Usage:
  python persona_builder.py --vertical Medicare
  python persona_builder.py --vertical Medicare --days 14
  python persona_builder.py --vertical Medicare --post   # Post to Discord
"""

import os, sys, json, argparse, datetime
from pathlib import Path

import requests
import yaml

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    from qdrant_sink import get_signals_since, QDRANT_ENABLED
except ImportError:
    QDRANT_ENABLED = False
    get_signals_since = None

# ── Config ───────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"
PERSONAS_DIR = Path(__file__).parent.parent / "personas"

def _load_config() -> dict:
    if CFG_PATH.exists():
        return yaml.safe_load(CFG_PATH.read_text()) or {}
    return {}

CFG = _load_config()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or CFG.get("llm", {}).get("api_key", "")
LLM_MODEL = CFG.get("llm", {}).get("model", "claude-sonnet-4-6")
WEBHOOK_URL = CFG.get("discord", {}).get("webhook_url") or os.getenv("DISCORD_WEBHOOK_URL", "")

# Baserow fallback config
BASEROW_KEY = CFG.get("baserow", {}).get("api_key", os.getenv("BASEROW_TOKEN", ""))
BASEROW_BASE_URL = CFG.get("baserow", {}).get("url", "https://baserow.pfsend.com")
REDDIT_TABLE = 818

PERSONA_SYSTEM_PROMPT = """You are Rachel, a performance creative strategist. Build a persona card from these real audience signals. Output:
- **Name** (a representative first name)
- **Age Range**
- **Core Pain** (their deepest frustration in one sentence)
- **Primary Emotion** (the dominant feeling driving their behavior)
- **Trigger Event** (the specific moment they became ready to buy)
- **Key Verbatim Phrases** (5 real quotes from the signals — use exact words)
- **Creative Angle** (how to speak to them in ads)
- **Hook Type** (which of the 17 hook types fits best for this persona)

Format as a clean Discord embed-ready markdown block. Use bold headers and keep it scannable. Do NOT use code fences — just clean markdown."""


# ── Signal fetching ──────────────────────────────────────────────────────────

def _fetch_from_qdrant(vertical: str, days: int) -> list[dict]:
    """Get audience signals from Qdrant."""
    if not QDRANT_ENABLED or not get_signals_since:
        return []
    signals = get_signals_since(days=days, vertical=vertical)
    # Filter to reddit-type signals
    return [s for s in signals if s.get("signal_type") == "reddit" or s.get("verbatim_hook")]


def _fetch_from_baserow(vertical: str) -> list[dict]:
    """Fallback: fetch from Baserow table 818."""
    if not BASEROW_KEY:
        return []
    url = f"{BASEROW_BASE_URL}/api/database/rows/table/{REDDIT_TABLE}/?user_field_names=true&size=50"
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Token {BASEROW_KEY}"},
            timeout=15,
        )
        r.raise_for_status()
        rows = r.json().get("results", [])
        if vertical:
            rows = [r for r in rows if (r.get("vertical") or "").lower() == vertical.lower()]
        return rows
    except Exception as e:
        print(f"[persona] Baserow fetch failed: {e}")
        return []


def fetch_signals(vertical: str, days: int) -> list[dict]:
    """Fetch audience signals from Qdrant, falling back to Baserow."""
    signals = _fetch_from_qdrant(vertical, days)
    if signals:
        print(f"[persona] {len(signals)} signals from Qdrant (vertical={vertical}, days={days})")
        return signals
    signals = _fetch_from_baserow(vertical)
    if signals:
        print(f"[persona] {len(signals)} signals from Baserow (vertical={vertical})")
    else:
        print(f"[persona] No signals found for vertical={vertical}")
    return signals


# ── Persona generation ───────────────────────────────────────────────────────

def _format_signals_for_prompt(signals: list[dict]) -> str:
    """Format signal list into a text block for Claude."""
    lines = []
    for i, s in enumerate(signals[:20], 1):  # Cap at 20 signals
        hook = s.get("verbatim_hook") or s.get("headline") or s.get("title", "")
        pain = s.get("pain_point") or s.get("summary", "")
        emotion = s.get("emotion", "")
        vertical = s.get("vertical", "")
        lines.append(f"Signal {i}:")
        if hook:
            lines.append(f"  Verbatim: \"{hook}\"")
        if pain:
            lines.append(f"  Pain/Summary: {pain}")
        if emotion:
            lines.append(f"  Emotion: {emotion}")
        if vertical:
            lines.append(f"  Vertical: {vertical}")
        lines.append("")
    return "\n".join(lines)


def generate_persona(vertical: str, signals: list[dict]) -> str:
    """Send signals to Claude and get a persona card back."""
    if not HAS_ANTHROPIC:
        print("[persona] anthropic package not installed — pip install anthropic")
        sys.exit(1)
    if not ANTHROPIC_API_KEY:
        print("[persona] No ANTHROPIC_API_KEY set")
        sys.exit(1)

    signals_text = _format_signals_for_prompt(signals)
    user_msg = f"Build a persona card for the **{vertical}** vertical based on these {len(signals)} audience signals:\n\n{signals_text}"

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=LLM_MODEL,
        max_tokens=1500,
        system=PERSONA_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    return response.content[0].text


# ── Output ───────────────────────────────────────────────────────────────────

def save_persona(vertical: str, persona_md: str) -> Path:
    """Save persona card to loop/personas/{vertical}_{date}.md."""
    PERSONAS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.date.today().strftime("%Y%m%d")
    slug = vertical.lower().replace(" ", "_")
    path = PERSONAS_DIR / f"{slug}_{date_str}.md"
    path.write_text(f"# Persona Card: {vertical}\n_Generated: {datetime.date.today().isoformat()}_\n\n{persona_md}\n")
    print(f"[persona] Saved to {path}")
    return path


def post_to_discord(vertical: str, persona_md: str):
    """Post persona card as Discord embed."""
    if not WEBHOOK_URL:
        print("[persona] No webhook_url configured — skipping Discord post")
        return

    embed = {
        "title": f"👤 Persona Card: {vertical}",
        "description": persona_md[:4000],
        "color": 0x9B59B6,
        "footer": {"text": "AutoResearchClaw v2.0 · persona_builder.py"},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        r = requests.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
        if r.status_code in (200, 204):
            print(f"[persona] Posted to Discord: {vertical}")
        else:
            print(f"[persona] Discord error {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"[persona] Discord post failed: {e}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Auto persona builder from audience signals")
    parser.add_argument("--vertical", required=True, help="Vertical to build persona for (e.g. Medicare)")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days (default: 7)")
    parser.add_argument("--post", action="store_true", help="Post persona card to Discord")
    args = parser.parse_args()

    signals = fetch_signals(args.vertical, args.days)
    if not signals:
        print(f"[persona] No signals found for {args.vertical} — skipping")
        sys.exit(0)

    print(f"[persona] Generating persona for {args.vertical} from {len(signals)} signals...")
    persona_md = generate_persona(args.vertical, signals)
    print(f"\n{persona_md}\n")

    save_persona(args.vertical, persona_md)

    if args.post:
        post_to_discord(args.vertical, persona_md)

    print(f"✅ Persona card complete for {args.vertical}")


if __name__ == "__main__":
    main()

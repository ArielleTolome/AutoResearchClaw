#!/usr/bin/env python3
"""
baserow_signals.py — Baserow → Discord signal bridge for AutoResearchClaw
Polls Baserow tables and fires typed signal cards to Discord channels.

Usage:
  python3 baserow_signals.py --mode news          # Check for new industry news rows
  python3 baserow_signals.py --mode sentiment      # Post vertical sentiment digest
  python3 baserow_signals.py --mode all            # Both
  python3 baserow_signals.py --demo               # Fire demo cards without state tracking

Config (env or config.yaml):
  BASEROW_TOKEN         — Baserow API token
  DISCORD_WEBHOOK_URL   — #creative-signals webhook
  BASEROW_URL           — base URL (default: https://baserow.pfsend.com)
  NEWS_TABLE_ID         — industry news table (default: 767)
  SENTIMENT_TABLE_ID    — vertical sentiment table (default: 768)
  STATE_FILE            — last-seen row ID state (default: .baserow_state.json)
"""

import os, sys, json, argparse, datetime, requests
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
BASEROW_URL       = os.getenv("BASEROW_URL", "https://baserow.pfsend.com")
BASEROW_TOKEN     = os.getenv("BASEROW_TOKEN", "Yg1DSyEipxerKG9cuVJg6p6OjN0RTN4R")
DISCORD_WEBHOOK   = os.getenv("DISCORD_WEBHOOK_URL", "")
NEWS_TABLE_ID     = int(os.getenv("NEWS_TABLE_ID", "767"))
SENTIMENT_TABLE_ID = int(os.getenv("SENTIMENT_TABLE_ID", "768"))
STATE_FILE        = Path(os.getenv("STATE_FILE", Path(__file__).parent.parent / "config" / ".baserow_state.json"))

SENTIMENT_EMOJI = {
    "Very Bullish": "🚀", "Bullish": "🟢", "Neutral": "🟡",
    "Bearish": "🔴", "Very Bearish": "💀",
}
ACTION_EMOJI = {
    "Scale Up": "📈", "Maintain": "➡️", "Monitor": "👀",
    "Reduce": "📉", "Exit": "🚫",
}
IMPACT_COLOR = {
    "Regulatory/Government": 0xe74c3c,   # red
    "Market/Competitive":    0xe67e22,   # orange
    "Financial":             0x3498db,   # blue
    "Technology":            0x9b59b6,   # purple
    "Consumer Behavior":     0x2ecc71,   # green
}
SENTIMENT_COLOR = {
    "Very Bullish": 0x27ae60, "Bullish": 0x2ecc71, "Neutral": 0xf39c12,
    "Bearish": 0xe67e22, "Very Bearish": 0xe74c3c,
}

# ── Baserow helpers ──────────────────────────────────────────────────────────

def baserow_get(table_id, params=None):
    url = f"{BASEROW_URL}/api/database/rows/table/{table_id}/"
    headers = {"Authorization": f"Token {BASEROW_TOKEN}"}
    p = {"user_field_names": "true", "size": 200}
    if params:
        p.update(params)
    r = requests.get(url, headers=headers, params=p, timeout=15)
    r.raise_for_status()
    return r.json()

# ── State tracking ───────────────────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ── Discord helpers ──────────────────────────────────────────────────────────

def send_embed(webhook_url, embed):
    if not webhook_url:
        print(f"[WARN] No webhook URL set — skipping send")
        print(f"  Would send: {embed.get('title','?')}")
        return
    r = requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
    if r.status_code not in (200, 204):
        print(f"[ERR] Discord {r.status_code}: {r.text[:200]}")
    else:
        print(f"[OK] Fired: {embed.get('title','?')}")

# ── Signal builders ──────────────────────────────────────────────────────────

def build_news_embed(row):
    vertical  = row.get("Vertical", {}).get("value", "Unknown")
    impact    = row.get("Impact Type", {}).get("value", "Unknown")
    sentiment = row.get("Sentiment Impact", {}).get("value", "Neutral")
    headline  = row.get("Headline", "No headline")
    source    = row.get("Source", "Unknown")
    source_url = row.get("Source URL", "")
    pub_date  = row.get("Published Date", "")
    summary   = row.get("Summary", "")
    actions   = row.get("Action Items", "")
    companies = row.get("Companies Affected", "")

    sent_emoji = SENTIMENT_EMOJI.get(sentiment, "⚪")
    color = IMPACT_COLOR.get(impact, 0x95a5a6)

    fields = []
    if summary:
        fields.append({"name": "📋 Summary", "value": summary[:1024], "inline": False})
    if actions:
        # Format numbered list
        fields.append({"name": "⚡ Action Items", "value": actions[:1024], "inline": False})
    if companies:
        fields.append({"name": "🏢 Companies Affected", "value": companies[:512], "inline": True})
    if pub_date:
        fields.append({"name": "📅 Published", "value": pub_date, "inline": True})

    return {
        "title": f"📰 LEADGEN_INDUSTRY_NEWS — {vertical}",
        "description": f"**{headline}**\n[{source}]({source_url})" if source_url else f"**{headline}**\n*{source}*",
        "color": color,
        "fields": fields,
        "footer": {"text": f"{sent_emoji} Sentiment: {sentiment}  |  Impact: {impact}  |  AutoResearchClaw"},
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }

def build_sentiment_digest(rows):
    """Build a vertical sentiment snapshot embed."""
    lines = []
    scale_up = []
    monitor  = []

    for row in rows:
        v       = row.get("Vertical", {}).get("value", "?")
        sent    = row.get("Current Sentiment", {}).get("value", "?")
        score   = row.get("Sentiment Score", "?")
        action  = row.get("Recommended Action", {}).get("value", "?")
        trend   = row.get("Trend", {}).get("value", "?")
        s_emoji = SENTIMENT_EMOJI.get(sent, "⚪")
        a_emoji = ACTION_EMOJI.get(action, "")
        lines.append(f"{s_emoji} **{v}** — {sent} ({score}/10) {a_emoji} {trend}")
        if action == "Scale Up":
            scale_up.append(v)
        elif action == "Monitor":
            monitor.append(v)

    fields = [{"name": "📊 All Verticals", "value": "\n".join(lines[:20]), "inline": False}]
    if scale_up:
        fields.append({"name": "📈 Scale Up", "value": ", ".join(scale_up), "inline": True})
    if monitor:
        fields.append({"name": "👀 Monitor", "value": ", ".join(monitor), "inline": True})

    return {
        "title": "📊 VERTICAL_SENTIMENT_DIGEST",
        "description": f"Leadgen vertical sentiment snapshot — {len(rows)} verticals tracked",
        "color": 0x3498db,
        "fields": fields,
        "footer": {"text": "AutoResearchClaw | Baserow sync"},
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }

# ── Modes ────────────────────────────────────────────────────────────────────

def run_news(webhook_url, demo=False):
    print("[news] Fetching industry news table...")
    data  = baserow_get(NEWS_TABLE_ID)
    rows  = data.get("results", [])
    state = load_state()
    last_seen = state.get("news_last_id", 0)

    new_rows = [r for r in rows if r["id"] > last_seen]
    if not new_rows and not demo:
        print(f"[news] No new rows (last_seen={last_seen})")
        return 0

    if demo:
        new_rows = rows  # fire all in demo mode

    fired = 0
    for row in new_rows:
        embed = build_news_embed(row)
        send_embed(webhook_url, embed)
        fired += 1

    if not demo and new_rows:
        state["news_last_id"] = max(r["id"] for r in new_rows)
        save_state(state)

    print(f"[news] Fired {fired} signal(s)")
    return fired

def run_sentiment(webhook_url, demo=False):
    print("[sentiment] Fetching vertical sentiment table...")
    data = baserow_get(SENTIMENT_TABLE_ID)
    rows = data.get("results", [])
    embed = build_sentiment_digest(rows)
    send_embed(webhook_url, embed)
    print(f"[sentiment] Digest fired ({len(rows)} verticals)")
    return 1

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Baserow → Discord signals for AutoResearchClaw")
    parser.add_argument("--mode", choices=["news", "sentiment", "all"], default="news")
    parser.add_argument("--demo", action="store_true", help="Fire all rows regardless of state")
    parser.add_argument("--webhook", help="Override Discord webhook URL")
    args = parser.parse_args()

    webhook = args.webhook or DISCORD_WEBHOOK

    total = 0
    if args.mode in ("news", "all"):
        total += run_news(webhook, demo=args.demo)
    if args.mode in ("sentiment", "all"):
        total += run_sentiment(webhook, demo=args.demo)

    print(f"\nDone — {total} signal(s) fired")

if __name__ == "__main__":
    main()

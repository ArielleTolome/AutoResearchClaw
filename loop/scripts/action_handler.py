#!/usr/bin/env python3
from __future__ import annotations
"""
action_handler.py — Intel-to-Brief in One Click (AutoResearchClaw v2.0)
Takes a signal ID and an action, fetches the signal from Baserow, calls
any Anthropic-compatible LLM (default: MiniMax Kimi M2.5-highspeed) with an action-specific
prompt, and posts the result to Discord. Model + base_url are read from config.yaml llm block.

Usage:
  python action_handler.py --signal-id abc123 --action generate_brief
  python action_handler.py --signal-id abc123 --action write_script --dry-run
  python action_handler.py --signal-id abc123 --action generate_hooks --output hooks.md
  python action_handler.py --signal-id abc123 --action image_concept --webhook-url https://...
"""

import os, sys, json, argparse, hashlib
from pathlib import Path

import requests
import yaml

try:
    from agent_runner import run_prompt as _agent_run_prompt
    HAS_AGENT_RUNNER = True
except ImportError:
    HAS_AGENT_RUNNER = False

try:
    from notion_sink import write_action_output as notion_write, HUB_URL as NOTION_HUB_URL
    HAS_NOTION = True
except ImportError:
    HAS_NOTION = False
    NOTION_HUB_URL = ""

# ── Config ───────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"
PROMPTS_PATH = Path(__file__).parent.parent / "config" / "prompts.ads.yaml"

def _load_config() -> dict:
    if CFG_PATH.exists():
        return yaml.safe_load(CFG_PATH.read_text()) or {}
    return {}

CFG = _load_config()
BASEROW_KEY = CFG.get("baserow", {}).get("api_key", os.getenv("BASEROW_TOKEN", ""))
BASEROW_BASE_URL = CFG.get("baserow", {}).get("url", "https://baserow.pfsend.com")
ANTHROPIC_API_KEY = (
    CFG.get("llm", {}).get("api_key")
    or os.getenv("MINIMAX_API_KEY")       # Kimi / MiniMax
    or os.getenv("ANTHROPIC_API_KEY")     # Anthropic Claude fallback
    or ""
)
LLM_MODEL      = CFG.get("llm", {}).get("model", "MiniMax-M2.5-highspeed")
LLM_BASE_URL   = CFG.get("llm", {}).get("base_url", None)  # e.g. https://api.minimax.io/anthropic
WEBHOOK_URL = CFG.get("discord", {}).get("webhook_url") or os.getenv("DISCORD_WEBHOOK_URL", "")

VALID_ACTIONS = [
    "generate_brief", "build_persona", "generate_hooks",
    "write_script", "image_concept",
]

# ── Action prompts ───────────────────────────────────────────────────────────

ACTION_PROMPTS = {
    "generate_brief": (
        "You are a senior ad creative strategist. Given this audience signal, "
        "produce a 1-page creative brief: Offer angle (2-3 sentences), Target persona "
        "(age, pain, desire, trigger event), 3 hook directions (each with hook text + "
        "hook type), Awareness stage assessment, Tone/format recommendation. "
        "Be specific and actionable."
    ),
    "build_persona": (
        "You are a media buyer and persona researcher. Build a detailed persona card "
        "from this signal: Name, Age range, Core pain (verbatim language from the signal), "
        "Primary desire, Trigger event that made them search, Top 3 objections, "
        "Emotional state, Awareness level (Schwartz 1-5), Recommended hook angle."
    ),
    "generate_hooks": (
        "You are a direct response copywriter. Generate 10 hooks for this signal using "
        "varied hook types: Question, Negative/Contrarian, Bold Claim, Social Proof, "
        "Statistic, Urgency, Curiosity Gap, Identification, Mechanism, Story Open. "
        "Label each with its type. Score each 1-10 for clarity and scroll-stop potential."
    ),
    "write_script": (
        "Write a 30-second ad script for this signal. Structure: "
        "[HOOK 0-3s] pattern interrupt or bold claim. "
        "[PROBLEM 3-8s] agitate the pain. "
        "[PROOF 8-18s] mechanism or social proof. "
        "[CTA 18-30s] clear action with urgency. "
        "Format as a script with timestamps and speaker notes."
    ),
    "image_concept": (
        "Describe 3 static image ad concepts for this signal. For each: "
        "Visual description (what we see), Text overlay (headline + subheadline), "
        "Emotional trigger it hits, Production notes (real photo vs illustration, "
        "color palette, style). Be specific enough for a designer or AI image "
        "generator to execute."
    ),
}

ACTION_EMOJI = {
    "generate_brief": "📋", "build_persona": "👤", "generate_hooks": "🎣",
    "write_script": "📝", "image_concept": "🖼️",
}
ACTION_LABEL = {
    "generate_brief": "Creative Brief", "build_persona": "Persona Card",
    "generate_hooks": "Hook Bank", "write_script": "Ad Script",
    "image_concept": "Image Concepts",
}


# ── Creative frameworks (optional) ──────────────────────────────────────────

def _load_creative_frameworks() -> str:
    """Load prompts.ads.yaml if it exists, for brief/hooks context."""
    if PROMPTS_PATH.exists():
        try:
            data = yaml.safe_load(PROMPTS_PATH.read_text()) or {}
            # Extract relevant framework text
            parts = []
            if "frameworks" in data:
                parts.append(yaml.dump(data["frameworks"], default_flow_style=False))
            if "hook_types" in data:
                parts.append(yaml.dump(data["hook_types"], default_flow_style=False))
            return "\n".join(parts)
        except Exception:
            pass
    return ""


# ── Baserow signal fetch ────────────────────────────────────────────────────

def fetch_signal(signal_id: str) -> dict | None:
    """Search Baserow tables for a signal matching the given ID."""
    for table_id in [767, 818, 813, 815]:
        url = f"{BASEROW_BASE_URL}/api/database/rows/table/{table_id}/?user_field_names=true&size=100"
        try:
            r = requests.get(
                url,
                headers={"Authorization": f"Token {BASEROW_KEY}"},
                timeout=15,
            )
            if not r.ok:
                continue
            rows = r.json().get("results", [])
            for row in rows:
                headline = row.get("headline") or row.get("Headline") or row.get("title") or row.get("hook_text") or ""
                row_id = hashlib.sha1(headline.encode()).hexdigest()[:12]
                if row_id == signal_id:
                    return {"source_table": table_id, **row}
        except Exception as e:
            print(f"[action] Baserow fetch failed (table {table_id}): {e}")
    return None


def _format_signal_for_prompt(signal: dict) -> str:
    """Format signal data into a readable text block for Claude."""
    lines = []
    table = signal.get("source_table")
    table_names = {767: "News", 818: "Reddit", 813: "Competitor Ads", 815: "Creative Briefs"}
    lines.append(f"Source: {table_names.get(table, f'Table {table}')}")

    field_map = [
        ("Headline", ["headline", "Headline", "title", "hook_text"]),
        ("Vertical", ["vertical", "Vertical"]),
        ("Summary", ["summary", "Summary", "pain_point"]),
        ("Emotion", ["emotion", "Sentiment Impact", "sentiment_impact"]),
        ("Source URL", ["source_url", "Source URL", "url"]),
        ("Subreddit", ["subreddit"]),
        ("Verbatim Hook", ["verbatim_hook"]),
        ("Ad Page", ["page_name"]),
        ("Spend", ["spend_bucket"]),
    ]
    for label, keys in field_map:
        for key in keys:
            val = signal.get(key)
            if val:
                if isinstance(val, dict):
                    val = val.get("value", "")
                if val:
                    lines.append(f"{label}: {val}")
                    break

    return "\n".join(lines)


# ── Claude call ─────────────────────────────────────────────────────────────

def run_action(action: str, signal: dict) -> str:
    """Call the configured LLM/agent with the action-specific prompt and signal data."""
    if not HAS_AGENT_RUNNER:
        print("[action] agent_runner not available — check loop/scripts/agent_runner.py")
        sys.exit(1)

    system_prompt = ACTION_PROMPTS[action]

    # Inject creative frameworks for brief/hooks actions
    if action in ("generate_brief", "generate_hooks"):
        frameworks = _load_creative_frameworks()
        if frameworks:
            system_prompt += f"\n\nUse these creative frameworks as reference:\n{frameworks}"

    signal_text = _format_signal_for_prompt(signal)
    user_msg = f"Here is the audience signal:\n\n{signal_text}\n\nExecute the action now."

    return _agent_run_prompt(system_prompt, user_msg, max_tokens=2000, config=CFG)


# ── Discord posting ─────────────────────────────────────────────────────────

def post_to_discord(title: str, content: str, action: str, signal_headline: str,
                    webhook_url: str | None = None):
    """Post action result as a Discord embed."""
    url = webhook_url or WEBHOOK_URL
    if not url:
        print("[WARN] No discord.webhook_url configured")
        return

    emoji = ACTION_EMOJI.get(action, "⚡")
    label = ACTION_LABEL.get(action, action)

    # Split content into chunks (Discord embed description max 4096)
    chunks = [content[i:i + 4000] for i in range(0, len(content), 4000)]

    # Post first chunk as main embed
    embed = {
        "title": f"{emoji} {label}: {signal_headline[:60]}",
        "description": chunks[0],
        "color": 0x5865F2,
        "footer": {"text": "AutoResearchClaw v2.0 · Intel-to-Brief"},
    }
    try:
        r = requests.post(url, json={"embeds": [embed]}, timeout=15)
        if r.status_code in (200, 204):
            print(f"[action] Posted to Discord: {label}")
        else:
            print(f"[action] Discord error {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"[action] Discord post failed: {e}")
        return

    # Post overflow chunks as plain messages
    for chunk in chunks[1:]:
        try:
            requests.post(url, json={"content": f"```{chunk}```"}, timeout=15)
        except Exception:
            pass


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Intel-to-Brief action handler (AutoResearchClaw v2.0)")
    parser.add_argument("--signal-id", required=True, help="Signal ID (SHA1 hash prefix from signal_cards)")
    parser.add_argument("--action", required=True, choices=VALID_ACTIONS, help="Action to run against the signal")
    parser.add_argument("--dry-run", action="store_true", help="Print output only — no API calls, no Discord post")
    parser.add_argument("--output", help="Write result to file path")
    parser.add_argument("--webhook-url", help="Override Discord webhook URL")
    args = parser.parse_args()

    print(f"[action] Signal: {args.signal_id} | Action: {args.action}")

    # Fetch signal from Baserow
    if args.dry_run:
        print("[DRY-RUN] Skipping Baserow fetch — using placeholder signal")
        signal = {
            "source_table": 0,
            "headline": f"Dry-run signal {args.signal_id}",
            "vertical": "Test",
            "summary": "This is a dry-run placeholder signal for testing.",
        }
    else:
        print(f"[action] Fetching signal {args.signal_id} from Baserow...")
        signal = fetch_signal(args.signal_id)
        if not signal:
            print(f"[ERROR] Signal {args.signal_id} not found in Baserow tables (767, 818, 813, 815)")
            sys.exit(1)

    headline = signal.get("headline") or signal.get("Headline") or signal.get("title") or signal.get("hook_text") or "Unknown"
    print(f"[action] Found: {headline[:80]}")

    # Run action
    if args.dry_run:
        print(f"\n[DRY-RUN] Would call Claude ({LLM_MODEL}) with action: {args.action}")
        print(f"[DRY-RUN] System prompt preview:\n  {ACTION_PROMPTS[args.action][:120]}...")
        print(f"[DRY-RUN] Signal data:\n{_format_signal_for_prompt(signal)}")
        return

    print(f"[action] Calling Claude ({LLM_MODEL})...")
    result = run_action(args.action, signal)
    print(f"\n{result}\n")

    # Save to file if requested
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result)
        print(f"[action] Saved to {out_path}")

    # Post to Discord
    webhook = args.webhook_url or WEBHOOK_URL
    post_to_discord(
        title=f"{ACTION_LABEL[args.action]}: {headline[:60]}",
        content=result,
        action=args.action,
        signal_headline=headline,
        webhook_url=webhook,
    )

    # Write to Notion
    if HAS_NOTION and CFG.get("notion", {}).get("api_key"):
        platform = CFG.get("notion", {}).get("default_platform", "Meta")
        notion_url = notion_write(args.action, signal, result, platform)
        if notion_url:
            print(f"[action] 📓 Notion page: {notion_url}")
        else:
            print("[action] ⚠️  Notion write failed (check api_key in config)")
    else:
        print("[action] ℹ️  Notion not configured — set notion.api_key in config.yaml to enable")

    print(f"✅ {ACTION_LABEL[args.action]} complete for signal {args.signal_id}")


if __name__ == "__main__":
    main()

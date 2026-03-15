"""
gate.py — Discord-based challenger approval gate.

Flow:
  1. Post challenger summary to Discord with reaction options
  2. Poll for reaction from authorized reactor(s)
  3. Return True on approval, False otherwise
"""

import argparse
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _extract(pattern: str, text: str, default: str = "—") -> str:
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else default


def post_challenger_to_discord(challenger_brief: str, config: dict) -> str:
    """
    Post the challenger to Discord for approval.
    Returns the message_id of the posted message.
    """
    gate_cfg = config.get("approval_gate", {})
    bot_token = gate_cfg.get("bot_token", "")
    channel_id = gate_cfg.get("channel_id", "1482366861650690200")

    name = _extract(r"CHALLENGER NAME:\s*(.+)", challenger_brief)
    hook = _extract(r"--- HOOK ---\n(.+?)---", challenger_brief)
    hypothesis = _extract(r"HYPOTHESIS:\s*(.+)", challenger_brief)
    angle = _extract(r"ANGLE:\s*(.+)", challenger_brief)

    timeout_hours = gate_cfg.get("timeout_hours", 4)
    timeout_action = gate_cfg.get("timeout_action", "kill")

    content = (
        "**🎯 New Challenger Ready for Approval**\n\n"
        f"**Name:** `{name}`\n"
        f"**Angle:** {angle}\n"
        f"**Hypothesis:** {hypothesis[:200]}\n\n"
        f"**Hook:** \"{hook[:150]}\"\n\n"
        "React with ✅ to **deploy** or ❌ to **reject**.\n"
        f"⏰ Auto-{timeout_action} in {timeout_hours}h if no reaction."
    )

    headers = {"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"}
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    resp = requests.post(url, headers=headers, json={"content": content})
    resp.raise_for_status()
    message_id = resp.json()["id"]

    for emoji in ["✅", "❌"]:
        encoded = requests.utils.quote(emoji)
        reaction_url = (
            f"https://discord.com/api/v10/channels/{channel_id}/messages/"
            f"{message_id}/reactions/{encoded}/@me"
        )
        reaction_resp = requests.put(reaction_url, headers=headers)
        reaction_resp.raise_for_status()
        time.sleep(0.3)

    print(f"[GATE] Posted challenger to Discord (message_id={message_id}). Waiting for reaction...")
    return message_id


def poll_for_reaction(message_id: str, config: dict) -> str:
    """
    Poll Discord for reaction from authorized users.
    Returns: "approved" | "rejected" | "timeout"
    """
    gate_cfg = config.get("approval_gate", {})
    bot_token = gate_cfg.get("bot_token", "")
    channel_id = gate_cfg.get("channel_id", "1482366861650690200")
    allowed_reactors = {str(user_id) for user_id in gate_cfg.get("allowed_reactors", [])}
    timeout_hours = gate_cfg.get("timeout_hours", 4)
    poll_interval_seconds = gate_cfg.get("poll_interval_seconds", 60)

    deadline = datetime.now() + timedelta(hours=timeout_hours)
    headers = {"Authorization": f"Bot {bot_token}"}

    while datetime.now() < deadline:
        time.sleep(poll_interval_seconds)

        for emoji, verdict in [("✅", "approved"), ("❌", "rejected")]:
            encoded = requests.utils.quote(emoji)
            url = f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}/reactions/{encoded}"
            resp = requests.get(url, headers=headers)
            if resp.status_code != 200:
                continue

            reactors = resp.json()
            for user in reactors:
                user_id = str(user.get("id", ""))
                if not allowed_reactors or user_id in allowed_reactors:
                    print(f"[GATE] {verdict.upper()} by user {user_id}")
                    return verdict

    print(f"[GATE] Timeout reached after {timeout_hours}h with no reaction")
    return "timeout"


def _log_gate_decision(message_id: str, decision: str, config: dict):
    """Append gate decision to learnings for loop awareness."""
    learnings_path = ROOT / "learnings" / "learnings.md"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n---\n\n### Gate Decision — {ts}\n\n**Decision:** {decision} (message_id={message_id})\n"
    with open(learnings_path, "a") as f:
        f.write(entry)


def run_gate(challenger_brief: str, dry_run: bool = False) -> bool:
    """
    Run the full approval gate.
    Returns True if challenger should be deployed, False otherwise.
    """
    config = load_config()
    gate_cfg = config.get("approval_gate", {})

    if not gate_cfg.get("enabled", False):
        print("[GATE] Approval gate disabled - auto-proceeding to deploy")
        return True

    if dry_run:
        print("[GATE] DRY RUN - auto-approving")
        return True

    message_id = post_challenger_to_discord(challenger_brief, config)
    result = poll_for_reaction(message_id, config)
    timeout_action = gate_cfg.get("timeout_action", "kill")

    if result == "approved":
        print("[GATE] Approved - proceeding to deploy")
        return True
    if result == "rejected":
        print("[GATE] Rejected - skipping deploy")
        _log_gate_decision(message_id, "rejected", config)
        return False

    if timeout_action == "deploy":
        print("[GATE] Timeout - auto-deploying (timeout_action=deploy)")
        return True
    if timeout_action == "skip_and_continue":
        print("[GATE] Timeout - skipping this challenger (timeout_action=skip_and_continue)")
        _log_gate_decision(message_id, "timeout_skipped", config)
        return False

    print("[GATE] Timeout - killing this challenger (timeout_action=kill)")
    _log_gate_decision(message_id, "timeout_killed", config)
    return False


def main():
    parser = argparse.ArgumentParser(description="AutoResearchClaw Discord approval gate")
    parser.add_argument("--challenger-file", help="Path to challenger brief markdown")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    brief = ""
    if args.challenger_file:
        brief = Path(args.challenger_file).read_text()
    run_gate(brief, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

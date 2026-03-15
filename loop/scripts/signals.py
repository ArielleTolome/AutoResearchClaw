"""
signals.py — Discord signal card posting for #creative-signals channel.

Posts typed embed cards to Discord via webhook for real-time loop observability.
Channel ID: 1482849535473614848

Integration hooks (where each signal should be called from):
  - emit_loop_start      → orchestrator.py run_cycle() start
  - emit_winner          → analyze.py when score > 7.0
  - emit_fatigue         → analyze.py when CTR drop > 20% week-over-week
  - emit_kill            → deploy.py when pausing a loser
  - emit_scale           → deploy.py when scaling winner budget
  - emit_challenger_ready → gate.py after challenger is written, before deploy
  - emit_brief_complete  → orchestrator.py after research pipeline completes
  - emit_loop_error      → orchestrator.py in exception handler
"""

from datetime import datetime
from enum import Enum
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"

SIGNALS_CHANNEL_ID = "1482849535473614848"


# ── Signal Types ────────────────────────────────────────────────────────────


class SignalType(Enum):
    WINNER = "🟢"
    FATIGUE = "🔴"
    CHALLENGER_READY = "🟡"
    KILL = "🔴"
    SCALE = "🟢"
    BRIEF_COMPLETE = "🔵"
    LOOP_START = "⚙️"
    LOOP_ERROR = "💥"


# ── Colors ──────────────────────────────────────────────────────────────────

SIGNAL_COLORS = {
    SignalType.WINNER: 0x00C851,
    SignalType.FATIGUE: 0xFF4444,
    SignalType.CHALLENGER_READY: 0xFFBB33,
    SignalType.KILL: 0xFF4444,
    SignalType.SCALE: 0x00C851,
    SignalType.BRIEF_COMPLETE: 0x0099CC,
    SignalType.LOOP_START: 0x9933CC,
    SignalType.LOOP_ERROR: 0xFF4444,
}


# ── Card Builders ───────────────────────────────────────────────────────────


def _build_winner_card(**kw) -> dict:
    ad_name = kw.get("ad_name", "Unknown Ad")
    hook_type = kw.get("hook_type", "—")
    hook_rate = kw.get("hook_rate", "—")
    ctr = kw.get("ctr", "—")
    cpa = kw.get("cpa", "—")
    score = kw.get("score", "—")
    offer_name = kw.get("offer_name", "—")
    platform = kw.get("platform", "—")

    desc = (
        f"**Ad:** {ad_name} — {hook_type} hook\n"
        f"**Hook Rate:** {hook_rate}% | **CTR:** {ctr}% | **CPA:** ${cpa}\n"
        f"**Score:** {score} / 10\n"
        f"**Offer:** {offer_name} | **Platform:** {platform}\n"
        f"**Recommended Action:** Scale budget 20% ↑"
    )
    return {"title": "🟢 WINNER DETECTED", "description": desc}


def _build_fatigue_card(**kw) -> dict:
    ad_name = kw.get("ad_name", "Unknown Ad")
    hook_type = kw.get("hook_type", "—")
    pct_drop = kw.get("pct_drop", "—")
    ctr_prev = kw.get("ctr_prev", "—")
    ctr_curr = kw.get("ctr_curr", "—")
    score = kw.get("score", "—")

    desc = (
        f"**Ad:** {ad_name} — {hook_type} hook\n"
        f"**CTR dropped** {pct_drop}% week-over-week ({ctr_prev}% → {ctr_curr}%)\n"
        f"**Score:** {score} / 10\n"
        f"**Recommended Action:** Kill or refresh hook"
    )
    return {"title": "🔴 FATIGUE ALERT", "description": desc}


def _build_challenger_ready_card(**kw) -> dict:
    ad_name = kw.get("ad_name", "Unknown Ad")
    hypothesis = kw.get("hypothesis", "—")
    angle = kw.get("angle", "—")
    hook_type = kw.get("hook_type", "—")
    awareness_stage = kw.get("awareness_stage", "—")

    desc = (
        f"**{ad_name}**\n"
        f"**Hypothesis:** {hypothesis}\n"
        f"**Angle:** {angle} | **Hook Type:** {hook_type}\n"
        f"**Awareness Stage:** {awareness_stage}\n\n"
        f"React ✅ to deploy · ❌ to skip"
    )
    return {"title": "🟡 CHALLENGER READY", "description": desc}


def _build_kill_card(**kw) -> dict:
    ad_name = kw.get("ad_name", "Unknown Ad")
    reason = kw.get("reason", "—")
    cpa = kw.get("cpa", "—")
    target_cpa = kw.get("target_cpa", "—")

    desc = (
        f"**{ad_name}**\n"
        f"**Reason:** {reason}\n"
        f"**CPA at kill:** ${cpa} (target: ${target_cpa})\n"
        f"**Marcel rule:** 3x CPA → kill"
    )
    return {"title": "🔴 AD KILLED", "description": desc}


def _build_scale_card(**kw) -> dict:
    ad_name = kw.get("ad_name", "Unknown Ad")
    new_budget = kw.get("new_budget", "—")
    trigger_reason = kw.get("trigger_reason", "—")

    desc = (
        f"**{ad_name}**\n"
        f"**New budget:** ${new_budget}/day (+20%)\n"
        f"**Trigger:** {trigger_reason}"
    )
    return {"title": "🟢 SCALING AD", "description": desc}


def _build_brief_complete_card(**kw) -> dict:
    topic = kw.get("topic", "—")
    platform = kw.get("platform", "—")
    angle_count = kw.get("angle_count", "—")

    desc = (
        f"**Topic:** {topic}\n"
        f"**Platform:** {platform}\n"
        f"**Key angles identified:** {angle_count}\n"
        f"**Baseline seeded** → loop ready"
    )
    return {"title": "🔵 BRIEF COMPLETE", "description": desc}


def _build_loop_start_card(**kw) -> dict:
    cycle_number = kw.get("cycle_number", "—")
    offer_name = kw.get("offer_name", "—")

    desc = (
        f"**Cycle:** {cycle_number}\n"
        f"**Offer:** {offer_name}\n"
        f"**Steps:** harvest → analyze → generate → deploy"
    )
    return {"title": "⚙️ LOOP CYCLE STARTING", "description": desc}


def _build_loop_error_card(**kw) -> dict:
    step = kw.get("step", "—")
    error_message = kw.get("error_message", "—")
    cycle_number = kw.get("cycle_number", "—")

    desc = (
        f"**Step:** {step}\n"
        f"**Error:** {error_message}\n"
        f"**Cycle:** {cycle_number}"
    )
    return {"title": "💥 LOOP ERROR", "description": desc}


CARD_BUILDERS = {
    SignalType.WINNER: _build_winner_card,
    SignalType.FATIGUE: _build_fatigue_card,
    SignalType.CHALLENGER_READY: _build_challenger_ready_card,
    SignalType.KILL: _build_kill_card,
    SignalType.SCALE: _build_scale_card,
    SignalType.BRIEF_COMPLETE: _build_brief_complete_card,
    SignalType.LOOP_START: _build_loop_start_card,
    SignalType.LOOP_ERROR: _build_loop_error_card,
}


# ── Core Emit ───────────────────────────────────────────────────────────────


def _get_webhook_url(config: dict) -> str | None:
    """Resolve webhook URL: discord.webhook_url → notifications.discord_webhook."""
    url = config.get("discord", {}).get("webhook_url")
    if url:
        return url
    return config.get("notifications", {}).get("discord_webhook")


def emit_signal(signal_type: SignalType, config: dict, **kwargs) -> dict | None:
    """
    Post a typed Discord embed card to #creative-signals.

    Returns the Discord API response JSON on success, None on failure or missing config.
    """
    webhook_url = _get_webhook_url(config)
    if not webhook_url:
        print(f"[signals] ⚠️  No Discord webhook URL configured — skipping {signal_type.name}")
        return None

    card = CARD_BUILDERS[signal_type](**kwargs)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    embed = {
        "title": card["title"],
        "description": card["description"],
        "color": SIGNAL_COLORS[signal_type],
        "footer": {"text": f"Rachel · AutoResearchClaw v1.1 · {timestamp}"},
    }

    payload = {"embeds": [embed]}

    # Append ?wait=true so Discord returns the message object (needed for reactions)
    post_url = webhook_url.rstrip("/")
    if "?" in post_url:
        post_url += "&wait=true"
    else:
        post_url += "?wait=true"

    try:
        resp = requests.post(post_url, json=payload, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[signals] ❌ Failed to post {signal_type.name}: {exc}")
        return None

    msg = resp.json()

    # CHALLENGER_READY: add approval reactions automatically
    if signal_type == SignalType.CHALLENGER_READY:
        _add_reactions(webhook_url, msg.get("id"), ["✅", "❌"])

    return msg


def _add_reactions(webhook_url: str, message_id: str | None, emojis: list[str]):
    """Add reaction emojis to a webhook message via Discord API."""
    if not message_id:
        return

    # Webhook URLs look like: https://discord.com/api/webhooks/{id}/{token}
    # We can use the webhook token to add reactions to messages it posted
    for emoji in emojis:
        try:
            encoded = requests.utils.quote(emoji)
            url = f"{webhook_url.rstrip('/')}/messages/{message_id}/reactions/{encoded}/@me"
            requests.put(url, timeout=10)
        except requests.RequestException:
            pass  # Non-critical — card was already posted


# ── Convenience Functions ───────────────────────────────────────────────────
# TODO: emit_loop_start → call from orchestrator.py run_cycle() start


def emit_loop_start(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.LOOP_START, config, **kwargs)


# TODO: emit_winner → call from analyze.py when score > 7.0


def emit_winner(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.WINNER, config, **kwargs)


# TODO: emit_fatigue → call from analyze.py when CTR drop > 20% week-over-week


def emit_fatigue(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.FATIGUE, config, **kwargs)


# TODO: emit_kill → call from deploy.py when pausing a loser


def emit_kill(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.KILL, config, **kwargs)


# TODO: emit_scale → call from deploy.py when scaling winner budget


def emit_scale(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.SCALE, config, **kwargs)


# TODO: emit_challenger_ready → call from gate.py after challenger is written, before deploy


def emit_challenger_ready(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.CHALLENGER_READY, config, **kwargs)


# TODO: emit_brief_complete → call from orchestrator.py after research pipeline completes


def emit_brief_complete(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.BRIEF_COMPLETE, config, **kwargs)


# TODO: emit_loop_error → call from orchestrator.py in exception handler


def emit_loop_error(config: dict, **kwargs) -> dict | None:
    return emit_signal(SignalType.LOOP_ERROR, config, **kwargs)


# ── CLI Usage ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Send a test signal to Discord")
    parser.add_argument("signal", choices=[s.name.lower() for s in SignalType], help="Signal type")
    args = parser.parse_args()

    cfg = yaml.safe_load(open(CONFIG_PATH))
    sig = SignalType[args.signal.upper()]
    result = emit_signal(sig, cfg, ad_name="Test Ad", cycle_number=0, step="test")

    if result:
        print(f"[signals] ✅ {sig.name} posted (message_id={result.get('id')})")
    else:
        print(f"[signals] ⚠️  {sig.name} not posted — check webhook config")

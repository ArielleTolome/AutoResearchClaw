#!/usr/bin/env python3
"""
discord_bot.py — Discord bot for AutoResearchClaw signal card interactions.

Posts signal cards as embeds WITH interactive buttons (Brief, Persona, Hooks,
Script, Image). Button clicks run action_handler.py and post results back.

Usage:
  python discord_bot.py          # Start the bot
  bash loop/bot/run_bot.sh       # Start via wrapper script
"""

import os, sys, json, asyncio, datetime, logging
from pathlib import Path

import yaml
import discord
from discord import app_commands
from discord.ext import commands

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("arc-bot")

# ── Config ───────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"

def _load_config() -> dict:
    if CFG_PATH.exists():
        return yaml.safe_load(CFG_PATH.read_text()) or {}
    return {}

CFG = _load_config()
BOT_TOKEN = CFG.get("discord", {}).get("bot_token") or os.getenv("DISCORD_BOT_TOKEN", "")
SIGNAL_CHANNEL_ID = int(
    CFG.get("discord", {}).get("signal_channel_id", 0)
    or os.getenv("DISCORD_SIGNAL_CHANNEL_ID", "0")
)
ACTION_HANDLER = Path(__file__).parent.parent / "scripts" / "action_handler.py"
OUTPUT_DIR = Path(__file__).parent.parent / "output"

# ── Action metadata ──────────────────────────────────────────────────────────
ACTION_EMOJI = {
    "generate_brief": "📋",
    "build_persona": "👤",
    "generate_hooks": "🎣",
    "write_script": "📝",
    "image_concept": "🖼️",
}
ACTION_LABEL = {
    "generate_brief": "Creative Brief",
    "build_persona": "Persona Card",
    "generate_hooks": "Hook Bank",
    "write_script": "Ad Script",
    "image_concept": "Image Concepts",
}

EMOTION_COLOR = {
    "Frustrated": 0xE74C3C, "Angry": 0xE74C3C,
    "Hopeful": 0x2ECC71, "Relieved": 0x2ECC71,
    "Confused": 0xF39C12, "Anxious": 0xE67E22,
    "Neutral": 0x95A5A6,
}
SOURCE_EMOJI = {"News": "📰", "Reddit": "💬"}


# ── Signal card buttons ──────────────────────────────────────────────────────

class SignalCardView(discord.ui.View):
    """Persistent view with 5 action buttons for a signal card."""

    def __init__(self, signal_id: str):
        super().__init__(timeout=None)
        buttons = [
            ("📋 Brief", "generate_brief"),
            ("👤 Persona", "build_persona"),
            ("🎣 Hooks", "generate_hooks"),
            ("📝 Script", "write_script"),
            ("🖼️ Image", "image_concept"),
        ]
        for label, action in buttons:
            btn = discord.ui.Button(
                label=label,
                custom_id=f"action:{action}:{signal_id}",
                style=discord.ButtonStyle.secondary,
            )
            self.add_item(btn)


# ── Bot setup ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    # Register persistent view so buttons work after restart
    bot.add_view(PersistentActionView())
    try:
        synced = await bot.tree.sync()
        log.info(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        log.error(f"Failed to sync commands: {e}")


# ── Slash command: /post-signals ─────────────────────────────────────────────

@bot.tree.command(name="post-signals", description="Post today's signal cards with action buttons")
async def post_signals(interaction: discord.Interaction):
    """Fetch today's signal cards JSON and post as embeds with buttons."""
    await interaction.response.defer(ephemeral=True)

    # Find today's signal cards file
    date_str = datetime.date.today().strftime("%Y%m%d")
    cards_path = OUTPUT_DIR / f"signal_cards_{date_str}.json"

    if not cards_path.exists():
        await interaction.followup.send(
            f"No signal cards found for today (`{cards_path.name}`). Run `signal_cards.py` first.",
            ephemeral=True,
        )
        return

    try:
        cards = json.loads(cards_path.read_text())
    except Exception as e:
        await interaction.followup.send(f"Failed to parse signal cards: {e}", ephemeral=True)
        return

    if not cards:
        await interaction.followup.send("Signal cards file is empty.", ephemeral=True)
        return

    # Determine target channel
    channel_id = SIGNAL_CHANNEL_ID or interaction.channel_id
    channel = bot.get_channel(channel_id) or interaction.channel

    posted = 0
    for card in cards[:10]:
        signal_id = card.get("signal_id", "unknown")
        source = card.get("source", "")
        vertical = card.get("vertical", "Unknown")
        headline = card.get("headline", "")[:200]
        summary = card.get("summary", "")[:200]
        source_url = card.get("source_url", "")
        emotion = card.get("emotion", "Neutral")
        color = EMOTION_COLOR.get(emotion, 0x95A5A6)
        emoji = SOURCE_EMOJI.get(source, "📡")

        desc_parts = [headline]
        if summary:
            desc_parts.append(f"\n{summary}")
        if source_url:
            desc_parts.append(f"\n[Source]({source_url})")

        embed = discord.Embed(
            title=f"{emoji} {vertical} · {source}",
            description="\n".join(desc_parts),
            color=color,
        )
        embed.set_footer(text="AutoResearchClaw v2.1 · Powered by Kimi M2.5")

        view = SignalCardView(signal_id)

        try:
            await channel.send(embed=embed, view=view)
            posted += 1
        except Exception as e:
            log.error(f"Failed to post card {signal_id}: {e}")

    await interaction.followup.send(
        f"Posted {posted}/{min(len(cards), 10)} signal cards to <#{channel.id}>.",
        ephemeral=True,
    )
    log.info(f"Posted {posted} signal cards to #{channel.name}")


# ── Persistent button handler ────────────────────────────────────────────────

class PersistentActionView(discord.ui.View):
    """Handles all action button interactions (persistent across restarts)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="placeholder", custom_id="action:noop:noop", style=discord.ButtonStyle.secondary)
    async def placeholder(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass  # Never matched — we use interaction_check + on_interaction instead


@bot.event
async def on_interaction(interaction: discord.Interaction):
    """Catch all button interactions with action:* custom IDs."""
    if interaction.type != discord.InteractionType.component:
        return

    custom_id = interaction.data.get("custom_id", "")
    if not custom_id.startswith("action:"):
        return

    parts = custom_id.split(":")
    if len(parts) != 3:
        return

    _, action, signal_id = parts

    if action not in ACTION_LABEL:
        await interaction.response.send_message("Unknown action.", ephemeral=True)
        return

    emoji = ACTION_EMOJI.get(action, "⚡")
    label = ACTION_LABEL.get(action, action)

    # Get signal headline from the original embed
    signal_headline = "Signal"
    if interaction.message and interaction.message.embeds:
        original_embed = interaction.message.embeds[0]
        # headline is the first line of the description
        if original_embed.description:
            signal_headline = original_embed.description.split("\n")[0][:60]

    # Defer — we'll edit this response when action_handler finishes
    await interaction.response.send_message(
        f"⏳ Running **{label}**...",
        ephemeral=False,
    )

    # Run action_handler.py as a subprocess
    output_path = Path(f"/tmp/arc_{signal_id}_{action}.md")

    cmd = [
        sys.executable,
        str(ACTION_HANDLER),
        "--signal-id", signal_id,
        "--action", action,
        "--output", str(output_path),
    ]

    log.info(f"Running: {' '.join(cmd)}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_text = stderr.decode()[-1500:] or stdout.decode()[-1500:] or "Unknown error"
            error_embed = discord.Embed(
                title=f"❌ {label} Failed",
                description=f"```\n{error_text[:4000]}\n```",
                color=0xE74C3C,
            )
            error_embed.set_footer(text="AutoResearchClaw v2.1")
            await interaction.edit_original_response(content=None, embed=error_embed)
            log.error(f"action_handler failed (exit {proc.returncode}): {error_text[:200]}")
            return

        # Read result from output file
        if output_path.exists():
            result_text = output_path.read_text()
        else:
            result_text = stdout.decode() or "No output generated."

        # Build result embed (chunk if >4000 chars)
        result_embed = discord.Embed(
            title=f"{emoji} {label}: {signal_headline[:60]}",
            description=result_text[:4000],
            color=0x5865F2,
        )
        result_embed.set_footer(text="AutoResearchClaw v2.1 · Powered by Kimi M2.5")

        await interaction.edit_original_response(content=None, embed=result_embed)

        # Post overflow chunks if result is longer than 4000 chars
        if len(result_text) > 4000:
            overflow_chunks = [result_text[i:i + 4000] for i in range(4000, len(result_text), 4000)]
            for chunk in overflow_chunks:
                overflow_embed = discord.Embed(
                    title=f"{emoji} {label} (continued)",
                    description=chunk,
                    color=0x5865F2,
                )
                await interaction.followup.send(embed=overflow_embed)

        log.info(f"Completed {label} for signal {signal_id}")

    except Exception as e:
        log.error(f"Error running action_handler: {e}")
        error_embed = discord.Embed(
            title=f"❌ {label} Error",
            description=f"```\n{str(e)[:4000]}\n```",
            color=0xE74C3C,
        )
        error_embed.set_footer(text="AutoResearchClaw v2.1")
        try:
            await interaction.edit_original_response(content=None, embed=error_embed)
        except Exception:
            pass


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        log.error("No bot token configured. Set discord.bot_token in config.yaml or DISCORD_BOT_TOKEN env var.")
        sys.exit(1)
    log.info("Starting AutoResearchClaw Discord bot...")
    bot.run(BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()

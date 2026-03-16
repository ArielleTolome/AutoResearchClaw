#!/usr/bin/env python3
"""
discord_bot.py — Discord bot for AutoResearchClaw signal card interactions.

Posts signal cards as embeds WITH interactive buttons (Brief, Persona, Hooks,
Script, Image). Button clicks run action_handler.py and post results back.

Usage:
  python discord_bot.py          # Start the bot
  bash loop/bot/run_bot.sh       # Start via wrapper script
"""

import os, sys, json, asyncio, datetime, logging, re
from pathlib import Path

import yaml
import aiohttp
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

# ── Helpers for v2.2 slash commands ──────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.parent / "scripts"
LEARNINGS_PATH = Path(__file__).parent.parent / "learnings" / "learnings.md"
BASELINE_PATH = Path(__file__).parent.parent / "config" / "baseline.md"

async def _run_script(script_name: str, args: list) -> tuple:
    proc = await asyncio.create_subprocess_exec(
        "/root/AutoResearchClaw/venv/bin/python3",
        str(SCRIPT_DIR / script_name), *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd="/root/AutoResearchClaw"
    )
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace")

async def _kimi(system: str, user: str, max_tokens: int = 2000) -> str:
    import anthropic as _ant
    client = _ant.Anthropic(api_key=CFG["llm"]["api_key"], base_url=CFG["llm"]["base_url"])
    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(None, lambda: client.messages.create(
        model=CFG["llm"]["model"], max_tokens=max_tokens,
        system=system, messages=[{"role":"user","content":user}]
    ))
    return next((b.text for b in resp.content if hasattr(b,"text")), "")

async def _post_embed(interaction, title, description, color):
    chunks = [description[i:i+4000] for i in range(0, len(description), 4000)]
    embed = discord.Embed(title=title, description=chunks[0], color=color)
    embed.set_footer(text="AutoResearchClaw v2.2 · Powered by Kimi M2.5")
    await interaction.followup.send(embed=embed)
    for chunk in chunks[1:]:
        await interaction.followup.send(embed=discord.Embed(description=chunk, color=color))


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
        GUILD = discord.Object(id=1482209550688981014)
        bot.tree.copy_global_to(guild=GUILD)
        synced = await bot.tree.sync(guild=GUILD)
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


# ── Slash commands v2.2 ──────────────────────────────────────────────────────

PLATFORM_CHOICES = [
    app_commands.Choice(name="meta", value="meta"),
    app_commands.Choice(name="tiktok", value="tiktok"),
    app_commands.Choice(name="youtube", value="youtube"),
    app_commands.Choice(name="native", value="native"),
    app_commands.Choice(name="general", value="general"),
]

PLATFORM_CHOICES_NO_GENERAL = [
    app_commands.Choice(name="meta", value="meta"),
    app_commands.Choice(name="tiktok", value="tiktok"),
    app_commands.Choice(name="youtube", value="youtube"),
    app_commands.Choice(name="native", value="native"),
]

AWARENESS_CHOICES = [
    app_commands.Choice(name="1-unaware", value="1-unaware"),
    app_commands.Choice(name="2-problem-aware", value="2-problem-aware"),
    app_commands.Choice(name="3-solution-aware", value="3-solution-aware"),
    app_commands.Choice(name="4-product-aware", value="4-product-aware"),
    app_commands.Choice(name="5-most-aware", value="5-most-aware"),
]


# ── 1. /research ─────────────────────────────────────────────────────────────

@bot.tree.command(name="research", description="Run full research pipeline on a topic")
@app_commands.describe(topic="The topic to research", platform="Target ad platform")
@app_commands.choices(platform=PLATFORM_CHOICES)
async def research(interaction: discord.Interaction, topic: str, platform: str = "general"):
    await interaction.response.defer()

    (rc1, reddit_out), (rc2, news_out), (rc3, competitor_out) = await asyncio.gather(
        _run_script("reddit_source.py", ["--limit", "10"]),
        _run_script("news_watcher.py", ["--limit", "5"]),
        _run_script("competitor_watcher.py", []),
    )

    synthesis = await _kimi(
        system="You are an ad creative strategist. Synthesize research into a structured intelligence report.",
        user=(
            f"Topic: {topic}\nPlatform: {platform}\n\n"
            f"Reddit:\n{reddit_out[-1000:]}\n\n"
            f"News:\n{news_out[-1000:]}\n\n"
            f"Competitors:\n{competitor_out[-1000:]}\n\n"
            "Deliver:\n"
            "1. Top 3 audience pain points with verbatim quotes\n"
            "2. Top 3 angles to test\n"
            "3. Awareness stage\n"
            "4. Key hook opportunities"
        ),
    )

    await _post_embed(interaction, f"🔍 Research: {topic}", synthesis, 0x2F80ED)

    # Also generate signal cards
    await _run_script("signal_cards.py", ["--output", "loop/output/"])


# ── 2. /full-brief ───────────────────────────────────────────────────────────

@bot.tree.command(name="full-brief", description="Generate a complete ACA creative brief")
@app_commands.describe(topic="The topic / offer to brief", platform="Target ad platform")
@app_commands.choices(platform=PLATFORM_CHOICES_NO_GENERAL)
async def full_brief(interaction: discord.Interaction, topic: str, platform: str = "meta"):
    await interaction.response.defer()

    brief = await _kimi(
        system=(
            "You are Rachel, an expert ad creative strategist trained in Ad Creative Academy methodology. "
            "Follow Schwartz Breakthrough Advertising doctrine. Be specific and tactical."
        ),
        user=(
            f"Topic/Offer: {topic}\nPlatform: {platform}\n\n"
            "Produce a FULL creative brief with these sections:\n"
            "1. AWARENESS STAGE — identify Schwartz stage and justify\n"
            "2. TARGET PERSONA — demographics, core pain, deepest desire, trigger event, top 3 objections, verbatim phrases\n"
            "3. TOP 3 ANGLES — each with hook type + psychological trigger\n"
            "4. HOOK BANK — 5 hooks per angle (15 total)\n"
            "5. AD CONCEPT — hook 0-3s / problem 3-8s / solution 8-15s / proof 15-22s / CTA 22-30s\n"
            "6. PLATFORM NOTES — specific creative specs and best practices for " + platform
        ),
        max_tokens=3000,
    )

    await _post_embed(interaction, f"📋 Full Brief: {topic}", brief, 0x27AE60)


# ── 3. /gen-hooks ────────────────────────────────────────────────────────────

@bot.tree.command(name="gen-hooks", description="Generate 20 hooks across all 17 ACA hook types")
@app_commands.describe(topic="The topic to generate hooks for", awareness_stage="Schwartz awareness stage")
@app_commands.choices(awareness_stage=AWARENESS_CHOICES)
async def gen_hooks(interaction: discord.Interaction, topic: str, awareness_stage: str = "2-problem-aware"):
    await interaction.response.defer()

    hooks = await _kimi(
        system="You are a direct response copywriter expert in ACA hook framework.",
        user=(
            f"Generate 20 hooks for: {topic}\n"
            f"Awareness stage: {awareness_stage}\n\n"
            "Cover all 17 hook types: Question, Shocking Stat, Contrarian, Story, How-To, "
            "Fear, Desire, Social Proof, Newsjacking, Empathy, Teaser, Negative, Bold Claim, "
            "Before/After, Authority, Urgency, Identification.\n\n"
            "Format each as:\n**#N [Type]** (score: X/10)\nHook text\n"
        ),
        max_tokens=2500,
    )

    await _post_embed(interaction, f"🎣 Hook Bank: {topic}", hooks, 0xF39C12)


# ── 4. /spy ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="spy", description="Search Anstrex for native ads on keyword")
@app_commands.describe(keyword="Keyword to spy on", days_running="Minimum days running")
async def spy(interaction: discord.Interaction, keyword: str, days_running: int = 30):
    await interaction.response.defer()

    _, comp_out = await _run_script("competitor_watcher.py", ["--vertical", keyword])

    # Fetch Anstrex API directly
    anstrex_text = ""
    try:
        url = "https://api.anstrex.com/api/v1/en/creative/search"
        headers = {
            "Authorization": "Bearer 631281|opCWb4Y22xWM1AidmVUjyLVAFe0y08F0uesjEQqy71c6a5b3",
            "Origin": "https://native.anstrex.com",
            "Referer": "https://native.anstrex.com/",
        }
        params = {
            "product_name": "native",
            "product_type": "creative",
            "keyword": keyword,
            "sort_id": 10,
            "page": 1,
            "additional_data": "eyJpcCI6bnVsbH0=",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ads = data.get("data", {}).get("data", [])[:5]
                    lines = []
                    for ad in ads:
                        src = ad.get("_source", {})
                        lines.append(
                            f"• **{src.get('title', 'N/A')}**\n"
                            f"  {src.get('sub_text', '')[:100]}\n"
                            f"  Networks: {', '.join(src.get('ad_network_names', []))}\n"
                            f"  Created: {src.get('created_at', 'N/A')}"
                        )
                    anstrex_text = "\n".join(lines) if lines else "No Anstrex results."
                else:
                    anstrex_text = f"Anstrex API returned {resp.status}"
    except Exception as e:
        anstrex_text = f"Anstrex fetch error: {e}"

    analysis = await _kimi(
        system="You are a competitive ad intelligence analyst. Analyze angle and hook patterns, then suggest counter-angles.",
        user=(
            f"Keyword: {keyword}\nMin days running: {days_running}\n\n"
            f"Competitor watcher output:\n{comp_out[-1000:]}\n\n"
            f"Anstrex top ads:\n{anstrex_text}\n\n"
            "Analyze:\n1. Dominant angles and hook patterns\n2. What's overused\n3. Counter-angle opportunities"
        ),
    )

    await _post_embed(interaction, f"🕵️ Spy: {keyword}", analysis, 0xE74C3C)


# ── 5. /daily-intel ──────────────────────────────────────────────────────────

@bot.tree.command(name="daily-intel", description="Run the full daily intel pipeline right now")
async def daily_intel(interaction: discord.Interaction):
    await interaction.response.defer()

    results = []
    for script in ["news_watcher.py", "reddit_source.py", "competitor_watcher.py", "intel_digest.py"]:
        rc, out = await _run_script(script, [])
        results.append((script, rc, out))

    # Also run signal cards
    rc_sc, sc_out = await _run_script("signal_cards.py", ["--output", "loop/output/"])
    results.append(("signal_cards.py", rc_sc, sc_out))

    # Parse counts from outputs
    all_output = " ".join(out for _, _, out in results)
    counts = re.findall(r"(\d+)\s+(new|relevant|ads? found|signal cards?)", all_output, re.IGNORECASE)
    count_str = "\n".join(f"• {num} {label}" for num, label in counts) if counts else "Pipeline complete."

    desc = f"**Counts:**\n{count_str}\n\n**Scripts run:** {len(results)}"
    for script, rc, out in results:
        status = "✅" if rc == 0 else "❌"
        desc += f"\n{status} `{script}` (exit {rc})"

    await _post_embed(interaction, "📰 Daily Intel Complete", desc, 0x9B59B6)


# ── 6. /loop-status ──────────────────────────────────────────────────────────

@bot.tree.command(name="loop-status", description="Show creative loop status and learnings")
async def loop_status(interaction: discord.Interaction):
    # Fast local reads — no defer needed
    learnings_text = ""
    learnings_words = 0
    if LEARNINGS_PATH.exists():
        full = LEARNINGS_PATH.read_text()
        learnings_words = len(full.split())
        learnings_text = full[-300:]

    baseline_preview = ""
    if BASELINE_PATH.exists():
        baseline_preview = BASELINE_PATH.read_text()[:200]

    offer_name = CFG.get("offer", {}).get("name", "N/A")
    offer_platform = CFG.get("offer", {}).get("platform", "N/A")
    target_cpa = CFG.get("offer", {}).get("target_cpa", "N/A")
    frequency = CFG.get("loop", {}).get("frequency_hours", "N/A")

    embed = discord.Embed(title="🔄 Loop Status", color=0x1ABC9C)
    embed.add_field(name="Offer", value=offer_name, inline=True)
    embed.add_field(name="Platform", value=offer_platform, inline=True)
    embed.add_field(name="Target CPA", value=str(target_cpa), inline=True)
    embed.add_field(name="Frequency", value=f"{frequency}h", inline=True)
    embed.add_field(name="Learnings", value=f"{learnings_words} words", inline=True)
    embed.add_field(name="Last Learnings", value=f"```\n{learnings_text[-200:]}\n```" if learnings_text else "None yet", inline=False)
    embed.add_field(name="Baseline Preview", value=f"```\n{baseline_preview}\n```" if baseline_preview else "Not set", inline=False)
    embed.set_footer(text="AutoResearchClaw v2.2 · Powered by Kimi M2.5")

    await interaction.response.send_message(embed=embed)


# ── 7. /score ────────────────────────────────────────────────────────────────

@bot.tree.command(name="score", description="Score ad copy against the ACA QA checklist")
@app_commands.describe(copy="The ad copy to evaluate")
async def score(interaction: discord.Interaction, copy: str):
    await interaction.response.defer()

    result = await _kimi(
        system="You are an ad copy QA specialist. Score ruthlessly.",
        user=(
            f"Score this ad copy:\n\n{copy}\n\n"
            "Score on 7 dimensions (each X/10 with one specific fix):\n"
            "1. Hook Clarity\n2. Awareness Match\n3. Emotional Driver\n"
            "4. Pain Agitation\n5. Proof Strength\n6. CTA Clarity\n7. Platform Fit\n\n"
            "Then:\nTOTAL: X/70\nVERDICT: LAUNCH / REVISE / KILL\nTOP 3 FIXES:\n- fix 1\n- fix 2\n- fix 3"
        ),
        max_tokens=1500,
    )

    # Parse total score to pick color
    match = re.search(r"TOTAL:\s*(\d+)/70", result)
    total = int(match.group(1)) if match else 0
    if total >= 50:
        color = 0x27AE60
    elif total >= 35:
        color = 0xF39C12
    else:
        color = 0xE74C3C

    await _post_embed(interaction, "📊 Copy Score", result, color)


# ── 8. /intel-digest ─────────────────────────────────────────────────────────

@bot.tree.command(name="intel-digest", description="Post the intel digest to Discord right now")
async def intel_digest(interaction: discord.Interaction):
    await interaction.response.defer()

    rc, out = await _run_script("intel_digest.py", [])

    if rc != 0:
        desc = f"**intel_digest.py failed (exit {rc})**\n```\n{out[-500:]}\n```"
    else:
        desc = out[-500:] if out.strip() else "Digest generated (no stdout output)."

    await _post_embed(interaction, "📰 Intel Digest", desc, 0x8E44AD)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        log.error("No bot token configured. Set discord.bot_token in config.yaml or DISCORD_BOT_TOKEN env var.")
        sys.exit(1)
    log.info("Starting AutoResearchClaw Discord bot...")
    bot.run(BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()

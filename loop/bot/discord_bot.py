#!/usr/bin/env python3
"""
discord_bot.py — Discord bot for AutoResearchClaw signal card interactions.

Posts signal cards as embeds WITH interactive buttons (Brief, Persona, Hooks,
Script, Image). Button clicks run action_handler.py and post results back.

Usage:
  python discord_bot.py          # Start the bot
  bash loop/bot/run_bot.sh       # Start via wrapper script
"""

import os, sys, json, asyncio, datetime, logging, re, argparse, hashlib
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
    """Call the configured LLM/agent (anthropic, openai, codex, or claude-code)."""
    try:
        import sys, pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))
        from agent_runner import run_prompt as _ar_run
    except ImportError:
        return "⚠️ agent_runner not available — check loop/scripts/agent_runner.py"

    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: _ar_run(system, user, max_tokens=max_tokens, config=CFG)),
            timeout=300,
        )
        return result if result.strip() else "⚠️ Model returned empty response. Try again."
    except asyncio.TimeoutError:
        return "⚠️ LLM request timed out after 300s. Try a shorter topic."
    except Exception as e:
        log.error(f"_kimi error: {e}")
        return f"⚠️ LLM error: {str(e)[:200]}"

async def _post_embed(interaction, title, description, color):
    if not description or not description.strip():
        description = "*(no output)*"
    chunks = [description[i:i+4000] for i in range(0, len(description), 4000)]
    embed = discord.Embed(title=title, description=chunks[0], color=color)
    embed.set_footer(text="AutoResearchClaw v2.2 · Powered by Kimi M2.5")
    await interaction.followup.send(embed=embed)
    for chunk in chunks[1:]:
        await interaction.followup.send(embed=discord.Embed(description=chunk, color=color))


def _extract_summary(content: str, max_chars: int = 280) -> str:
    """Pull first meaningful paragraph from output as a summary snippet."""
    lines = [l.strip() for l in content.split("\n") if l.strip() and not l.startswith("#")]
    snippet = " ".join(lines)[:max_chars]
    if len(snippet) == max_chars:
        snippet = snippet.rsplit(" ", 1)[0] + "…"
    return snippet or content[:max_chars]


async def _download_attachment(attachment: discord.Attachment) -> str:
    """Download a Discord attachment to /tmp, return local path."""
    ext = attachment.filename.rsplit(".", 1)[-1].lower() if "." in attachment.filename else "mp4"
    hash_str = hashlib.md5(attachment.url.encode()).hexdigest()[:8]
    local_path = f"/tmp/arc_upload_{hash_str}.{ext}"
    async with aiohttp.ClientSession() as session:
        async with session.get(attachment.url) as resp:
            if resp.status == 200:
                with open(local_path, "wb") as f:
                    f.write(await resp.read())
    return local_path


def _notion_link_view(notion_url: str) -> discord.ui.View:
    """Return a View with a single 'Open in Notion' link button."""
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Open in Notion",
        style=discord.ButtonStyle.link,
        url=notion_url,
        emoji="📓",
    ))
    return view


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

        # Extract Notion URL from action_handler stdout (line: "[action] 📓 Notion page: <url>")
        notion_url = None
        notion_match = re.search(r'Notion page:\s*(https://www\.notion\.so/\S+)', stdout.decode())
        if notion_match:
            notion_url = notion_match.group(1)

        # Build compact summary card — full content lives in Notion
        summary = _extract_summary(result_text)
        result_embed = discord.Embed(
            title=f"{emoji} {label}: {signal_headline[:60]}",
            description=summary,
            color=0x5865F2,
        )
        if notion_url:
            result_embed.add_field(name="📓 Full Output", value=f"[View in Notion]({notion_url})", inline=False)
        result_embed.set_footer(text="AutoResearchClaw v2.2 · Powered by Kimi M2.5")

        view = _notion_link_view(notion_url) if notion_url else discord.utils.MISSING
        await interaction.edit_original_response(content=None, embed=result_embed, view=view)

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

BATCH_PLATFORM_CHOICES = [
    app_commands.Choice(name="Meta", value="meta"),
    app_commands.Choice(name="TikTok", value="tiktok"),
    app_commands.Choice(name="Native", value="native"),
    app_commands.Choice(name="YouTube", value="youtube"),
]

BATCH_SIZE_CHOICES = [
    app_commands.Choice(name="15 ads (3 concepts × 5 hooks)", value="15"),
    app_commands.Choice(name="45 ads (+ 3 actors)", value="45"),
    app_commands.Choice(name="90 ads (+ music variations)", value="90"),
    app_commands.Choice(name="180 ads (full matrix)", value="180"),
]


# ── 1. /research ─────────────────────────────────────────────────────────────

@bot.tree.command(name="research", description="Run full research pipeline on a topic")
@app_commands.describe(topic="The topic to research", platform="Target ad platform")
@app_commands.choices(platform=PLATFORM_CHOICES)
async def research(interaction: discord.Interaction, topic: str, platform: str = "general"):
    # Acknowledge immediately — scripts take time and Discord token expires after 15 min
    await interaction.response.send_message(
        f"🔍 Researching **{topic}** ({platform}) — this takes ~60s…"
    )

    try:
        (rc1, reddit_out), (rc2, news_out), (rc3, competitor_out) = await asyncio.gather(
            _run_script("reddit_source.py", ["--limit", "10"]),
            _run_script("news_watcher.py", ["--limit", "5"]),
            _run_script("competitor_watcher.py", []),
        )

        synthesis = await _kimi(
            system="You are an ad creative strategist. Be concise. Synthesize research into a structured intelligence report.",
            user=(
                f"Topic: {topic}\nPlatform: {platform}\n\n"
                f"Reddit:\n{reddit_out[-600:]}\n\n"
                f"News:\n{news_out[-600:]}\n\n"
                f"Competitors:\n{competitor_out[-600:]}\n\n"
                "Deliver (bullet points, be brief):\n"
                "1. Top 3 audience pain points with verbatim quotes\n"
                "2. Top 3 angles to test\n"
                "3. Awareness stage\n"
                "4. Key hook opportunities"
            ),
            max_tokens=1000,
        )

        # Save to Notion
        notion_url = None
        try:
            from notion_sink import write_intel as _ni
            signal = {"headline": f"Research: {topic}", "vertical": "Other",
                      "summary": _extract_summary(synthesis), "source": "Research", "emotion": "Neutral"}
            notion_url = _ni(signal)
        except Exception as e:
            log.warning(f"Notion write failed: {e}")

        # Build compact embed
        pain_match = re.findall(r'(?:\d+\.\s+)(.{20,120})', synthesis)
        preview = "\n".join(f"• {p.strip()}" for p in pain_match[:3]) or _extract_summary(synthesis, 280)

        embed = discord.Embed(title=f"🔍 Research: {topic}", description=preview, color=0x2F80ED)
        embed.add_field(name="Platform", value=platform.title(), inline=True)
        if notion_url:
            embed.add_field(name="📓 Full Report", value=f"[View in Notion]({notion_url})", inline=False)
        embed.set_footer(text="AutoResearchClaw v2.2 · Powered by Kimi M2.5")

        view = _notion_link_view(notion_url) if notion_url else discord.utils.MISSING
        await interaction.edit_original_response(content=None, embed=embed, view=view)

        # Generate signal cards in background
        asyncio.create_task(_run_script("signal_cards.py", ["--output", "loop/output/"]))

    except Exception as e:
        log.error(f"/research error: {e}")
        await interaction.edit_original_response(content=f"❌ Research failed: {str(e)[:200]}")


# ── 2. /full-brief ───────────────────────────────────────────────────────────

@bot.tree.command(name="full-brief", description="Generate a complete ACA creative brief")
@app_commands.describe(topic="The topic / offer to brief", platform="Target ad platform")
@app_commands.choices(platform=PLATFORM_CHOICES_NO_GENERAL)
async def full_brief(interaction: discord.Interaction, topic: str, platform: str = "meta"):
    await interaction.response.send_message(f"📋 Building brief for **{topic}** ({platform})…")

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

    # Save to Notion and post compact card
    notion_url = None
    try:
        from notion_sink import write_brief as _nb
        signal = {"headline": topic, "vertical": "Other", "summary": _extract_summary(brief), "signal_id": ""}
        notion_url = _nb(signal, brief, platform)
    except Exception as e:
        log.warning(f"Notion write failed: {e}")

    summary = _extract_summary(brief)
    embed = discord.Embed(title=f"📋 Full Brief: {topic}", description=summary, color=0x27AE60)
    embed.add_field(name="Platform", value=platform.title(), inline=True)
    if notion_url:
        embed.add_field(name="📓 Full Brief", value=f"[View in Notion]({notion_url})", inline=False)
    embed.set_footer(text="AutoResearchClaw v2.2 · Powered by Kimi M2.5")
    view = _notion_link_view(notion_url) if notion_url else discord.utils.MISSING
    try:
        await interaction.edit_original_response(content=None, embed=embed, view=view)
    except Exception:
        await interaction.followup.send(embed=embed, view=view)


# ── 3. /gen-hooks ────────────────────────────────────────────────────────────

@bot.tree.command(name="gen-hooks", description="Generate 20 hooks across all 17 ACA hook types")
@app_commands.describe(topic="The topic to generate hooks for", awareness_stage="Schwartz awareness stage")
@app_commands.choices(awareness_stage=AWARENESS_CHOICES)
async def gen_hooks(interaction: discord.Interaction, topic: str, awareness_stage: str = "2-problem-aware"):
    await interaction.response.send_message(f"🎣 Generating hooks for **{topic}**…")

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

    # Save to Notion and post compact card
    notion_url = None
    try:
        from notion_sink import write_hooks as _nh
        signal = {"headline": topic, "vertical": "Other", "summary": _extract_summary(hooks), "signal_id": ""}
        notion_url = _nh(signal, hooks, "meta")
    except Exception as e:
        log.warning(f"Notion write failed: {e}")

    # Show top 3 hooks as a preview field
    hook_lines = re.findall(r'\*\*#\d+.*?\*\*.*?\n(.+)', hooks)
    preview = "\n".join(f"• {h.strip()}" for h in hook_lines[:3]) or _extract_summary(hooks, 200)

    embed = discord.Embed(title=f"🎣 Hook Bank: {topic}", description=preview, color=0xF39C12)
    embed.add_field(name="Stage", value=awareness_stage.replace("-", " ").title(), inline=True)
    embed.add_field(name="Count", value="20 hooks", inline=True)
    if notion_url:
        embed.add_field(name="📓 Full Hook Bank", value=f"[View in Notion]({notion_url})", inline=False)
    embed.set_footer(text="AutoResearchClaw v2.2 · Powered by Kimi M2.5")
    view = _notion_link_view(notion_url) if notion_url else discord.utils.MISSING
    try:
        await interaction.edit_original_response(content=None, embed=embed, view=view)
    except Exception:
        await interaction.followup.send(embed=embed, view=view)


# ── 4. /spy ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="spy", description="Search Anstrex for native ads on keyword")
@app_commands.describe(keyword="Keyword to spy on", days_running="Minimum days running")
async def spy(interaction: discord.Interaction, keyword: str, days_running: int = 30):
    await interaction.response.send_message(f"🕵️ Spying on **{keyword}** (min {days_running} days running)…")
    try:
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

        embed = discord.Embed(title=f"🕵️ Spy: {keyword}", description=analysis[:4000], color=0xE74C3C)
        embed.set_footer(text="AutoResearchClaw v2.2 · Powered by Kimi M2.5")
        await interaction.edit_original_response(content=None, embed=embed)
    except Exception as e:
        log.error(f"/spy error: {e}")
        await interaction.edit_original_response(content=f"❌ Spy failed: {str(e)[:200]}")


# ── 5. /daily-intel ──────────────────────────────────────────────────────────

@bot.tree.command(name="daily-intel", description="Run the full daily intel pipeline right now")
async def daily_intel(interaction: discord.Interaction):
    await interaction.response.send_message("📰 Running daily intel pipeline — this takes ~2 min…")
    try:
        results = []
        scripts = ["news_watcher.py", "reddit_source.py", "competitor_watcher.py", "intel_digest.py", "signal_cards.py"]
        for i, script in enumerate(scripts, 1):
            args = ["--output", "loop/output/"] if script == "signal_cards.py" else []
            rc, out = await _run_script(script, args)
            results.append((script, rc, out))
            # Update progress mid-run so the user sees something
            if i < len(scripts):
                status_line = " · ".join(
                    ("✅" if r == 0 else "❌") + f" `{s}`" for s, r, _ in results
                )
                await interaction.edit_original_response(content=f"📰 Running… {status_line}")

        all_output = " ".join(out for _, _, out in results)
        counts = re.findall(r"(\d+)\s+(new|relevant|ads? found|signal cards?)", all_output, re.IGNORECASE)
        count_str = "\n".join(f"• {num} {label}" for num, label in counts) if counts else "Pipeline complete."

        desc = f"**Counts:**\n{count_str}\n\n"
        for script, rc, out in results:
            status = "✅" if rc == 0 else "❌"
            desc += f"{status} `{script}` (exit {rc})\n"

        embed = discord.Embed(title="📰 Daily Intel Complete", description=desc[:4000], color=0x9B59B6)
        embed.set_footer(text="AutoResearchClaw v2.2 · Powered by Kimi M2.5")
        await interaction.edit_original_response(content=None, embed=embed)
    except Exception as e:
        log.error(f"/daily-intel error: {e}")
        await interaction.edit_original_response(content=f"❌ Daily intel failed: {str(e)[:200]}")


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
    await interaction.response.send_message("📊 Scoring your copy…")
    try:
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

        match = re.search(r"TOTAL:\s*(\d+)/70", result)
        total = int(match.group(1)) if match else 0
        color = 0x27AE60 if total >= 50 else (0xF39C12 if total >= 35 else 0xE74C3C)

        embed = discord.Embed(title="📊 Copy Score", description=result[:4000], color=color)
        embed.set_footer(text="AutoResearchClaw v2.2 · Powered by Kimi M2.5")
        await interaction.edit_original_response(content=None, embed=embed)
    except Exception as e:
        log.error(f"/score error: {e}")
        await interaction.edit_original_response(content=f"❌ Score failed: {str(e)[:200]}")


# ── 8. /intel-digest ─────────────────────────────────────────────────────────

@bot.tree.command(name="intel-digest", description="Post the intel digest to Discord right now")
async def intel_digest(interaction: discord.Interaction):
    await interaction.response.send_message("📰 Running intel digest…")
    try:
        rc, out = await _run_script("intel_digest.py", [])
        if rc != 0:
            desc = f"**intel_digest.py failed (exit {rc})**\n```\n{out[-500:]}\n```"
        else:
            desc = out[-500:] if out.strip() else "Digest generated (no stdout output)."
        embed = discord.Embed(title="📰 Intel Digest", description=desc[:4000], color=0x8E44AD)
        embed.set_footer(text="AutoResearchClaw v2.2 · Powered by Kimi M2.5")
        await interaction.edit_original_response(content=None, embed=embed)
    except Exception as e:
        log.error(f"/intel-digest error: {e}")
        await interaction.edit_original_response(content=f"❌ Intel digest failed: {str(e)[:200]}")


# ── 9. /batch ────────────────────────────────────────────────────────────────

@bot.tree.command(name="batch", description="Generate a full batch of ad variations (15-180 unique ads)")
@app_commands.describe(
    topic="Topic or offer description",
    concept_id="Concept vault ID (e.g. c001) — overrides topic",
    platform="Ad platform",
    batch_size="How many unique ads to generate",
)
@app_commands.choices(platform=BATCH_PLATFORM_CHOICES, batch_size=BATCH_SIZE_CHOICES)
async def batch(interaction: discord.Interaction, topic: str, concept_id: str = "", platform: str = "meta", batch_size: str = "15"):
    await interaction.response.send_message(f"⚙️ Building **{batch_size}-ad batch** for **{topic or concept_id}** ({platform})…")

    args = ["--topic", topic, "--platform", platform, "--batch-size", batch_size]
    if concept_id:
        args = ["--concept-id", concept_id, "--platform", platform, "--batch-size", batch_size]

    rc, out = await _run_script("batch_generator.py", args)

    # Extract summary from output
    summary = out[-2000:] if len(out) > 2000 else out
    # Try to find the multiplication table line
    total_match = re.search(r'(\d+)\s+unique ads?', out, re.IGNORECASE)
    total_ads = total_match.group(0) if total_match else batch_size + " ads"

    status = "✅" if rc == 0 else "❌"
    embed = discord.Embed(
        title=f"⚙️ Batch: {topic or concept_id}",
        description=f"{status} Generated **{total_ads}**\n\n```{summary[:1500]}```",
        color=0x9B59B6,
    )
    embed.add_field(name="Platform", value=platform.title(), inline=True)
    embed.add_field(name="Target Size", value=batch_size + " ads", inline=True)
    embed.set_footer(text="AutoResearchClaw v2.3 · The Batch Machine")
    try:
        await interaction.edit_original_response(content=None, embed=embed)
    except Exception:
        await interaction.followup.send(embed=embed)


# ── 10. /concept-status ───────────────────────────────────────────────────────

@bot.tree.command(name="concept-status", description="Show the concept vault — proven concepts, testing, ready to iterate")
async def concept_status(interaction: discord.Interaction):
    await interaction.response.defer()

    vault_path = Path(__file__).parent.parent / "learnings" / "concept_vault.json"
    if not vault_path.exists():
        await interaction.followup.send("📭 No concept vault found. Use `/batch` to start building concepts.")
        return

    import json as _json
    concepts = _json.loads(vault_path.read_text())

    proven = [c for c in concepts if c["status"] in ("proven", "scaling")]
    testing = [c for c in concepts if c["status"] == "testing"]
    dead = [c for c in concepts if c["status"] == "dead"]

    embed = discord.Embed(title="🧠 Concept Vault", color=0x27AE60)

    if proven:
        lines = []
        for c in proven:
            hr = f"{c['hook_rate_best']:.0%}" if c.get("hook_rate_best") else "—"
            ctr = f"{c['ctr_best']:.1%}" if c.get("ctr_best") else "—"
            lines.append(f"**{c['concept_id']}** {c['name'][:40]}\n↳ Batches: {c['batch_count']} | Hook Rate: {hr} | CTR: {ctr}")
        embed.add_field(name=f"✅ Proven / Scaling ({len(proven)})", value="\n\n".join(lines)[:1024], inline=False)

    if testing:
        lines = []
        for c in testing:
            lines.append(f"**{c['concept_id']}** {c['name'][:40]} — {c['batch_count']} batch(es)")
        embed.add_field(name=f"🧪 Testing ({len(testing)})", value="\n".join(lines)[:1024], inline=False)

    total = len(concepts)
    embed.set_footer(text=f"Total: {total} concepts ({len(dead)} dead) · 80% iteration / 20% new")

    await interaction.followup.send(embed=embed)


# ── 11. /tracker ─────────────────────────────────────────────────────────────

TRACKER_STATUS_CHOICES = [
    app_commands.Choice(name="All", value="all"),
    app_commands.Choice(name="Live", value="live"),
    app_commands.Choice(name="Waiting", value="waiting"),
    app_commands.Choice(name="Winners", value="winner"),
    app_commands.Choice(name="Dead", value="dead"),
]

@bot.tree.command(name="tracker", description="View and manage the ACA creative testing tracker")
@app_commands.describe(
    view="Which rows to show",
    offer="Filter by offer name",
    digest="Post today's tracker digest to the channel",
)
@app_commands.choices(view=TRACKER_STATUS_CHOICES)
async def tracker(interaction: discord.Interaction, view: str = "all", offer: str = "", digest: bool = False):
    await interaction.response.defer()

    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        import creative_tracker as ct

        if digest:
            ct.cmd_digest(argparse.Namespace())
            await interaction.followup.send("✅ Tracker digest posted to Discord.")
            return

        status_filter = None if view == "all" else view
        rows = ct._get_rows(status_filter=status_filter, offer_filter=offer or None)

        if not rows:
            await interaction.followup.send(f"📭 No rows found (filter: {view}, offer: {offer or 'any'}).")
            return

        # Group by status
        from collections import defaultdict
        groups = defaultdict(list)
        for row in rows:
            s = (row.get("field_8016") or {}).get("value", "Waiting")
            groups[s].append(row)

        STATUS_EMOJI = {"Live": "🟢", "Waiting": "⏳", "Winner": "🏆", "Dead": "🔴", "Paused": "⏸️"}

        embed = discord.Embed(
            title=f"📊 Creative Tracker{' — ' + offer if offer else ''}",
            color=0x3498DB,
        )

        for status in ["Winner", "Live", "Waiting", "Paused", "Dead"]:
            group = groups.get(status, [])
            if not group:
                continue
            emoji = STATUS_EMOJI.get(status, "•")
            lines = []
            for row in group[:8]:
                cid = row.get("field_8013") or "—"
                hook = (row.get("field_8018") or "—")[:40]
                hr = row.get("field_8032")
                ctr = row.get("field_8031")
                roas = row.get("field_8028")
                cpa = row.get("field_8029")
                m = []
                if hr: m.append(f"HR:{float(hr):.0%}")
                if ctr: m.append(f"CTR:{float(ctr):.1%}")
                if roas: m.append(f"ROAS:{float(roas):.1f}x")
                if cpa: m.append(f"CPA:${float(cpa):.2f}")
                metric_str = " | ".join(m)
                lines.append(f"`{cid}` {hook}" + (f"\n  ↳ {metric_str}" if metric_str else ""))

            embed.add_field(
                name=f"{emoji} {status} ({len(group)})",
                value="\n".join(lines)[:1024],
                inline=False,
            )

        embed.set_footer(text=f"AutoResearchClaw v2.3 · creative_testing_tracker (Baserow 819) · {len(rows)} rows shown")
        await interaction.followup.send(embed=embed)

    except Exception as e:
        log.error(f"/tracker error: {e}")
        await interaction.followup.send(f"❌ Tracker error: {str(e)[:300]}")


# ── Main ─────────────────────────────────────────────────────────────────────

# ── 13. /sprint ──────────────────────────────────────────────────────────────

# ── 12. /reframe ─────────────────────────────────────────────────────────────

REFRAME_AWARENESS_CHOICES = [
    app_commands.Choice(name="Not specified", value=""),
    app_commands.Choice(name="1-unaware", value="1-unaware"),
    app_commands.Choice(name="2-problem-aware", value="2-problem-aware"),
    app_commands.Choice(name="3-solution-aware", value="3-solution-aware"),
    app_commands.Choice(name="4-product-aware", value="4-product-aware"),
    app_commands.Choice(name="5-most-aware", value="5-most-aware"),
]

@bot.tree.command(name="reframe", description="Generate 7 psychological offer reframes with audience intel from Qdrant")
@app_commands.describe(
    offer="The offer/product to reframe (e.g. 'ACA health insurance')",
    audience="Target audience (e.g. 'uninsured Americans 25-55')",
    current_angle="What angle you're currently running (optional — helps find fresh frames)",
    skip_intel="Skip Qdrant audience intel pull",
)
async def reframe(interaction: discord.Interaction, offer: str, audience: str = "",
                  current_angle: str = "", skip_intel: bool = False):
    await interaction.response.send_message(
        f"🔄 Reframing **{offer}**{' for ' + audience if audience else ''}…\n"
        f"{'📡 Pulling audience intel from Qdrant…' if not skip_intel else '⏭️ Skipping intel pull'}"
    )

    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from offer_reframe import reframe_offer

        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: reframe_offer(
                offer=offer,
                audience=audience,
                current_angle=current_angle,
                with_intel=not skip_intel,
                config=CFG,
            )),
            timeout=300,
        )

        if not result or not result.strip():
            await interaction.edit_original_response(content="⚠️ Reframe returned empty. Try again.")
            return

        # Save to Notion
        notion_url = None
        try:
            from notion_sink import write_action_output as _nw
            signal = {"headline": f"Reframe: {offer}", "vertical": "Other",
                      "summary": _extract_summary(result), "signal_id": "reframe"}
            notion_url = _nw(signal, "offer_reframe", result)
        except Exception as e:
            log.warning(f"Notion write failed: {e}")

        # Extract top pick for the preview
        top_pick = ""
        tp_match = re.search(r'(?:TOP PICK|🏆).*?\n+(.*?)(?:\n##|\Z)', result, re.DOTALL)
        if tp_match:
            top_pick = tp_match.group(1).strip()[:300]

        # Count reframes found
        reframe_count = len(re.findall(r'###?\s*\[?\d', result))

        summary = top_pick or _extract_summary(result, 300)

        embed = discord.Embed(
            title=f"🔄 Offer Reframes: {offer[:60]}",
            description=summary,
            color=0x9B59B6,
        )
        if audience:
            embed.add_field(name="Audience", value=audience[:100], inline=True)
        if current_angle:
            embed.add_field(name="Current Angle", value=current_angle[:100], inline=True)
        embed.add_field(name="Reframes Generated", value=str(reframe_count or 7), inline=True)
        embed.add_field(name="Intel", value="✅ Qdrant" if not skip_intel else "⏭️ Skipped", inline=True)

        if notion_url:
            embed.add_field(name="📓 Full Analysis", value=f"[View in Notion]({notion_url})", inline=False)
        embed.set_footer(text="AutoResearchClaw v2.7 · Offer Reframe Engine · Schwartz + Hopkins + Hormozi")

        # Also post the full result as a follow-up if no Notion
        view = _notion_link_view(notion_url) if notion_url else discord.utils.MISSING
        await interaction.edit_original_response(content=None, embed=embed, view=view)

        # If no Notion, post full result as chunked embeds
        if not notion_url:
            chunks = [result[i:i+4000] for i in range(0, len(result), 4000)]
            for i, chunk in enumerate(chunks[:4]):  # Max 4 follow-ups
                follow_embed = discord.Embed(
                    description=chunk,
                    color=0x9B59B6,
                )
                if i == len(chunks[:4]) - 1:
                    follow_embed.set_footer(text="AutoResearchClaw v2.7 · Offer Reframe Engine")
                await interaction.followup.send(embed=follow_embed)

    except asyncio.TimeoutError:
        await interaction.edit_original_response(content="⚠️ Reframe timed out after 5 min. Try a simpler offer description.")
    except Exception as e:
        log.error(f"/reframe error: {e}")
        try:
            await interaction.edit_original_response(content=f"❌ Reframe error: {str(e)[:300]}")
        except Exception:
            await interaction.followup.send(f"❌ Reframe error: {str(e)[:300]}")


# ── 13. /sprint ──────────────────────────────────────────────────────────────

SPRINT_VIEW_CHOICES = [
    app_commands.Choice(name="All", value="all"),
    app_commands.Choice(name="Planning", value="planning"),
    app_commands.Choice(name="In Progress", value="in progress"),
    app_commands.Choice(name="Ready for Review", value="ready for review"),
    app_commands.Choice(name="Notes Given", value="notes given"),
    app_commands.Choice(name="Ready to Launch", value="ready to launch"),
    app_commands.Choice(name="Launched", value="launched"),
]

@bot.tree.command(name="sprint", description="View the creative production sprint queue")
@app_commands.describe(view="Filter by status", offer="Filter by offer name")
@app_commands.choices(view=SPRINT_VIEW_CHOICES)
async def sprint(interaction: discord.Interaction, view: str = "all", offer: str = ""):
    await interaction.response.defer()
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        import sprint_planner as sp

        status_filter = None if view == "all" else view
        rows = sp.get_jobs(status=status_filter, offer=offer or None)

        if not rows:
            await interaction.followup.send(
                f"📭 No jobs found (filter: {view}, offer: {offer or 'any'})."
            )
            return

        from collections import defaultdict
        groups = defaultdict(list)
        for row in rows:
            s = (row.get(f"field_{sp.F_STATUS}") or {}).get("value", "Planning")
            groups[s].append(row)

        KANBAN_ORDER_BOT = [
            "Planning", "In Progress", "Ready for Review",
            "Notes Given", "Ready to Launch", "Launched", "On Hold", "Killed",
        ]
        STATUS_EMOJI_BOT = {
            "Planning": "📐", "In Progress": "🔨", "Ready for Review": "👀",
            "Notes Given": "📝", "Ready to Launch": "🚀", "Launched": "✅",
            "On Hold": "⏸️", "Killed": "💀",
        }

        title = "🏃 Sprint Queue"
        if offer:
            title += f" — {offer}"
        if view != "all":
            title += f" [{view.title()}]"

        embed = discord.Embed(title=title, color=0x9B59B6)

        for status in KANBAN_ORDER_BOT:
            group = groups.get(status, [])
            if not group:
                continue
            if view != "all" and status.lower() != view.lower():
                continue
            emoji = STATUS_EMOJI_BOT.get(status, "•")
            lines = []
            for row in group[:6]:
                job_name = row.get(f"field_{sp.F_JOB_NAME}") or f"row:{row['id']}"
                job_type = (row.get(f"field_{sp.F_JOB_TYPE}") or {}).get("value", "—")
                angle = (row.get(f"field_{sp.F_ANGLE}") or "")[:30]
                lines.append(f"`{job_name}` — {job_type}" + (f"\n  ↳ {angle}" if angle else ""))
            if len(group) > 6:
                lines.append(f"…and {len(group) - 6} more")
            embed.add_field(
                name=f"{emoji} {status} ({len(group)})",
                value="\n".join(lines)[:1024],
                inline=False,
            )

        embed.set_footer(text=f"AutoResearchClaw v2.4 · production_queue (Baserow 820) · {len(rows)} total jobs")
        await interaction.followup.send(embed=embed)

    except Exception as e:
        log.error(f"/sprint error: {e}")
        try:
            await interaction.edit_original_response(content=f"❌ Sprint error: {str(e)[:300]}")
        except Exception:
            await interaction.followup.send(f"❌ Sprint error: {str(e)[:300]}")


# ── 14. /qa ───────────────────────────────────────────────────────────────────

QA_PLATFORM_CHOICES = [
    app_commands.Choice(name="Meta", value="meta"),
    app_commands.Choice(name="TikTok", value="tiktok"),
    app_commands.Choice(name="YouTube", value="youtube"),
]

@bot.tree.command(name="qa", description="Run AI QA checklist on a creative job")
@app_commands.describe(
    job_name="Job name (e.g. SA0001_...) or Baserow row ID",
    platform="Platform to check against",
)
@app_commands.choices(platform=QA_PLATFORM_CHOICES)
async def qa(interaction: discord.Interaction, job_name: str, platform: str = "meta"):
    await interaction.response.defer()
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        import qa_checklist as qc
        import sprint_planner as sp

        # Resolve job_name → row_id
        row_id = None
        if job_name.isdigit():
            row_id = int(job_name)
        else:
            # Search by job name
            rows = sp.get_jobs()
            for row in rows:
                if (row.get(f"field_{sp.F_JOB_NAME}") or "").strip().lower() == job_name.strip().lower():
                    row_id = row["id"]
                    break
            if row_id is None:
                await interaction.followup.send(
                    f"❌ Job `{job_name}` not found in production queue. Use the full job name or row ID."
                )
                return

        result = qc.run_qa_with_kimi(row_id=row_id, platform=platform)

        passed = result["passed"]
        score = result["score"]
        total = result["total"]
        failed_items = result["failed_items"]
        notes = result["notes"]
        jname = result.get("job_name", str(row_id))

        # Apply result to Baserow
        qc._apply_qa_result(row_id, passed=passed, failed_items=failed_items, dry_run=False)

        color = 0x2ECC71 if passed else 0xE74C3C
        result_str = "✅ PASS" if passed else "❌ FAIL"

        embed = discord.Embed(
            title=f"QA {result_str}: {jname}",
            description=notes[:500] if notes else "",
            color=color,
        )
        embed.add_field(name="Score", value=f"{score}/{total} ({score/total:.0%})", inline=True)
        embed.add_field(name="Platform", value=platform.title(), inline=True)

        if failed_items:
            failed_text = "\n".join(f"• {item[:80]}" for item in failed_items[:8])
            embed.add_field(name="❌ Failed Checks", value=failed_text[:1024], inline=False)

        status_set = "Ready to Launch" if passed else "Notes Given"
        embed.add_field(name="Status Updated", value=status_set, inline=False)
        embed.set_footer(text="AutoResearchClaw v2.4 · QA Gate · ACA Course 501")

        try:
            await interaction.edit_original_response(content=None, embed=embed)
        except Exception:
            await interaction.followup.send(embed=embed)

    except Exception as e:
        log.error(f"/qa error: {e}")
        try:
            await interaction.edit_original_response(content=f"❌ QA error: {str(e)[:300]}")
        except Exception:
            await interaction.followup.send(f"❌ QA error: {str(e)[:300]}")


# ── 15. /ugc-brief ───────────────────────────────────────────────────────────

@bot.tree.command(name="ugc-brief", description="Generate a UGC creator brief (ACA Course 502)")
@app_commands.describe(
    concept_id="Concept ID (e.g. c001)",
    offer="Offer name (e.g. Stimulus Assistance)",
    niche="Product niche (e.g. government benefits, supplements)",
)
async def ugc_brief(interaction: discord.Interaction, concept_id: str, offer: str, niche: str):
    await interaction.response.send_message(
        f"📋 Generating UGC brief for **{concept_id}** ({offer})…"
    )
    try:
        system_prompt = (
            "You are Rachel, an expert creative director specializing in UGC (User Generated Content) ads. "
            "Generate a full UGC creator brief following the ACA Course 502 template exactly.\n\n"
            "Include ALL of the following sections:\n"
            "1. **Checklist** — what footage to capture (8-10 bullet points, specific and actionable)\n"
            "2. **Shooting Tips** — lighting, audio, technical specs (1080p 30fps vertical), environment\n"
            "3. **Step 1: You and your [niche]** — tell me about your peeves/concerns/stories "
            "(2-3 prompts with example responses showing what good answers look like)\n"
            "4. **Step 2: You and your [product]** — cover: first impressions, your ritual/routine, "
            "what it feels like, how it works, results you've seen, and the CTA\n\n"
            "Format guidelines:\n"
            "- Be conversational, warm, and encouraging — address the creator as 'you'\n"
            "- Be hyper-specific to the offer and niche (not generic)\n"
            "- Each step should have 3-5 prompts with example answers in italics\n"
            "- End with a brief Note to Creator reminding them to be authentic"
        )

        user_prompt = (
            f"Concept ID: {concept_id}\n"
            f"Offer: {offer}\n"
            f"Niche: {niche}\n\n"
            f"Generate the full UGC creator brief for this concept."
        )

        result = await _kimi(system=system_prompt, user=user_prompt, max_tokens=3000)

        title = f"🎬 UGC Brief: {concept_id} — {offer}"
        await _post_embed(interaction, title=title, description=result, color=0xE67E22)

    except Exception as e:
        log.error(f"/ugc-brief error: {e}")
        try:
            await interaction.edit_original_response(content=f"❌ UGC brief failed: {str(e)[:200]}")
        except Exception:
            await interaction.followup.send(f"❌ UGC brief failed: {str(e)[:200]}")


# ── 16. /ad-dissect ──────────────────────────────────────────────────────────

@bot.tree.command(name="ad-dissect", description="Dissect a competitor or reference ad with Gemini video AI")
@app_commands.describe(
    url="YouTube URL or local file path (optional if uploading file)",
    file="Upload a video or image file directly",
    offer="Your offer (for counter-angle suggestions)",
)
async def ad_dissect(interaction: discord.Interaction, url: str = None, file: discord.Attachment = None, offer: str = ""):
    # Resolve source: attachment > url
    local_temp = None
    source_arg = None
    if file is not None:
        await interaction.response.send_message(f"🔬 Downloading attachment `{file.filename}` and running Gemini ad dissection…")
        local_temp = await _download_attachment(file)
        source_arg = local_temp
    elif url:
        await interaction.response.send_message(f"🔬 Running Gemini ad dissection on `{url[:80]}`…")
        source_arg = url
    else:
        await interaction.response.send_message(embed=discord.Embed(
            title="❌ No input provided",
            description="Please provide a URL or upload a video/image file.",
            color=0xE74C3C
        ))
        return
    try:
        script = SCRIPT_DIR / "ad_dissect.py"
        args = ["--video", source_arg]
        if offer:
            args += ["--offer", offer]

        rc, out = await _run_script("ad_dissect.py", args)

        # Parse JSON output
        data = {}
        try:
            data = json.loads(out.strip())
        except json.JSONDecodeError:
            start = out.find("{")
            end = out.rfind("}") + 1
            if start != -1 and end > start:
                try:
                    data = json.loads(out[start:end])
                except json.JSONDecodeError:
                    pass

        if "error" in data:
            embed = discord.Embed(
                title="🔬 Ad Dissection — Error",
                description=data.get("error", "Unknown error")[:500],
                color=0xE74C3C,
            )
            await interaction.edit_original_response(content=None, embed=embed)
            return

        hook = data.get("hook_transcript", "N/A")[:200]
        hook_type = data.get("hook_type", "N/A")
        stage = data.get("awareness_stage", "N/A")
        angle = data.get("angle", "N/A")
        hook_rate = data.get("predicted_hook_rate", "N/A")
        score = data.get("overall_score", 0)
        strengths = data.get("strengths", [])
        weaknesses = data.get("weaknesses", [])
        counter_angles = data.get("counter_angle_opportunities", [])
        reasoning = data.get("score_reasoning", "")

        embed = discord.Embed(
            title="🔬 Ad Dissection",
            description=f"Source: {url[:200]}",
            color=0x9B59B6,
        )
        embed.add_field(name="🎯 Hook", value=f'"{hook}" [{hook_type}]', inline=False)
        embed.add_field(name="📊 Awareness Stage", value=stage, inline=True)
        embed.add_field(name="🔀 Angle", value=angle, inline=True)
        embed.add_field(name="📈 Predicted Hook Rate", value=hook_rate, inline=True)
        embed.add_field(name="🏆 Score", value=f"{score}/100", inline=True)

        if strengths:
            embed.add_field(name="✅ Strengths", value="\n".join(f"• {s}" for s in strengths[:5])[:1024], inline=False)
        if weaknesses:
            embed.add_field(name="⚠️ Weaknesses", value="\n".join(f"• {w}" for w in weaknesses[:3])[:1024], inline=False)
        if counter_angles:
            embed.add_field(name="💡 Counter-Angles", value="\n".join(f"• {c}" for c in counter_angles[:3])[:1024], inline=False)
        if reasoning:
            embed.add_field(name="💭 Reasoning", value=reasoning[:512], inline=False)
        if offer:
            embed.add_field(name="🎁 Offer", value=offer, inline=True)

        embed.set_footer(text="AutoResearchClaw v2.5 · Gemini Video Intelligence · ACA Methodology")

        await interaction.edit_original_response(content=None, embed=embed)

    except Exception as e:
        log.error(f"/ad-dissect error: {e}")
        try:
            await interaction.edit_original_response(content=f"❌ Ad dissection error: {str(e)[:300]}")
        except Exception:
            await interaction.followup.send(f"❌ Ad dissection error: {str(e)[:300]}")
    finally:
        if local_temp and os.path.exists(local_temp):
            os.remove(local_temp)


# ── 17. /video-qa ─────────────────────────────────────────────────────────────

@bot.tree.command(name="video-qa", description="Run Gemini QA on a video creative before launch")
@app_commands.describe(
    url="YouTube URL or local file path (optional if uploading file)",
    file="Upload a video or image file directly",
    platform="Target platform",
    job_name="Job name to update in sprint queue (optional)",
)
@app_commands.choices(platform=[
    app_commands.Choice(name="Meta", value="meta"),
    app_commands.Choice(name="TikTok", value="tiktok"),
    app_commands.Choice(name="YouTube", value="youtube"),
])
async def video_qa_cmd(interaction: discord.Interaction, url: str = None,
                       file: discord.Attachment = None,
                       platform: app_commands.Choice[str] = None,
                       job_name: str = ""):
    platform_val = platform.value if platform else "meta"
    # Resolve source: attachment > url
    local_temp = None
    source_arg = None
    if file is not None:
        await interaction.response.send_message(f"🎬 Downloading attachment `{file.filename}` and running Gemini QA [{platform_val}]…")
        local_temp = await _download_attachment(file)
        source_arg = local_temp
    elif url:
        await interaction.response.send_message(f"🎬 Running Gemini QA on `{url[:80]}` [{platform_val}]…")
        source_arg = url
    else:
        await interaction.response.send_message(embed=discord.Embed(
            title="❌ No input provided",
            description="Please provide a URL or upload a video/image file.",
            color=0xE74C3C
        ))
        return
    try:
        args = ["--video", source_arg, "--platform", platform_val]
        if job_name:
            args += ["--job-name", job_name, "--update-baserow"]

        rc, out = await _run_script("video_qa.py", args)

        data = {}
        try:
            data = json.loads(out.strip())
        except json.JSONDecodeError:
            start = out.find("{")
            end = out.rfind("}") + 1
            if start != -1 and end > start:
                try:
                    data = json.loads(out[start:end])
                except json.JSONDecodeError:
                    pass

        passed = data.get("overall_pass", False)
        qa_score = data.get("qa_score", 0)
        recommendation = data.get("recommendation", "")
        issues = data.get("issues", [])
        duration = data.get("duration_seconds", "?")
        detected_ratio = data.get("detected_ratio", "?")
        color = 0x27AE60 if passed else 0xE74C3C
        result_str = "✅ PASS" if passed else "❌ FAIL"

        title = f"🎬 Video QA {result_str}"
        if job_name:
            title += f": {job_name}"

        embed = discord.Embed(title=title, description=url[:200], color=color)
        embed.add_field(name="Platform", value=platform_val.title(), inline=True)
        embed.add_field(name="QA Score", value=f"{qa_score}/100", inline=True)
        embed.add_field(name="Duration", value=f"{duration}s", inline=True)
        embed.add_field(name="Ratio", value=detected_ratio, inline=True)

        if recommendation:
            embed.add_field(name="💬 Recommendation", value=recommendation[:500], inline=False)

        if issues:
            issues_text = "\n".join(
                f"[{i.get('timestamp','?')}] **{i.get('severity','?').upper()}**: {i.get('description','')}"
                for i in issues[:8]
            )
            embed.add_field(name="⚠️ Issues Found", value=issues_text[:1024], inline=False)

        if job_name:
            status_set = "Ready to Launch" if passed else "Notes Given"
            embed.add_field(name="Status Updated", value=status_set, inline=False)

        embed.set_footer(text="AutoResearchClaw v2.5 · Gemini Video Intelligence")
        await interaction.edit_original_response(content=None, embed=embed)

    except Exception as e:
        log.error(f"/video-qa error: {e}")
        try:
            await interaction.edit_original_response(content=f"❌ Video QA error: {str(e)[:300]}")
        except Exception:
            await interaction.followup.send(f"❌ Video QA error: {str(e)[:300]}")
    finally:
        if local_temp and os.path.exists(local_temp):
            os.remove(local_temp)


# ── 18. /competitor-spy ───────────────────────────────────────────────────────

@bot.tree.command(name="competitor-spy", description="Spy on competitor video ads with Gemini analysis")
@app_commands.describe(
    keyword="Search keyword (e.g. stimulus check) — optional if uploading a file",
    file="Upload a competitor video or image ad directly",
    source="Data source (keyword search only)",
    limit="Max ads to analyze (1-5) (keyword search only)",
)
@app_commands.choices(
    source=[
        app_commands.Choice(name="Foreplay", value="foreplay"),
        app_commands.Choice(name="Facebook Ads Library", value="fb"),
    ],
    limit=[
        app_commands.Choice(name="1 ad", value=1),
        app_commands.Choice(name="2 ads", value=2),
        app_commands.Choice(name="3 ads", value=3),
        app_commands.Choice(name="5 ads", value=5),
    ],
)
async def competitor_spy(
    interaction: discord.Interaction,
    keyword: str = None,
    file: discord.Attachment = None,
    source: app_commands.Choice[str] = None,
    limit: app_commands.Choice[int] = None,
):
    source_val = source.value if source else "foreplay"
    limit_val = str(limit.value) if limit else "3"
    # Resolve source: attachment > keyword search
    local_temp = None
    if file is not None:
        await interaction.response.send_message(f"🕵️ Downloading attachment `{file.filename}` and running competitor spy analysis…")
        local_temp = await _download_attachment(file)
        args_base = ["--video", local_temp, "--post-discord"]
    elif keyword:
        await interaction.response.send_message(
            f"🕵️ Running competitor spy for **{keyword}** [{source_val}, {limit_val} ads]…"
        )
        args_base = ["--keyword", keyword, "--source", source_val, "--limit", limit_val, "--post-discord"]
    else:
        await interaction.response.send_message(embed=discord.Embed(
            title="❌ No input provided",
            description="Please provide a search keyword or upload a video/image file.",
            color=0xE74C3C
        ))
        return
    try:
        args = args_base

        rc, out = await _run_script("competitor_video_spy.py", args)

        # Parse output
        data = {}
        try:
            data = json.loads(out.strip())
        except json.JSONDecodeError:
            start = out.find("{")
            end = out.rfind("}") + 1
            if start != -1 and end > start:
                try:
                    data = json.loads(out[start:end])
                except json.JSONDecodeError:
                    pass

        ads_analyzed = data.get("ads_analyzed", 0)
        dissections = data.get("dissections", [])

        # Build summary embed
        angles = [d.get("angle", "") for d in dissections if "error" not in d and d.get("angle")]
        hook_types = [d.get("hook_type", "") for d in dissections if "error" not in d and d.get("hook_type")]

        from collections import Counter
        angle_summary = ", ".join(f"{k} ({v})" for k, v in Counter(angles).most_common(3)) or "N/A"
        hook_summary = ", ".join(f"{k} ({v})" for k, v in Counter(hook_types).most_common(3)) or "N/A"

        spy_title = f"🕵️ Competitor Spy Complete: \"{keyword}\"" if keyword else f"🕵️ Competitor Spy Complete: {file.filename if file else 'upload'}"
        spy_desc = f"Analyzed **{ads_analyzed}** ads via {source_val}" if keyword else f"Analyzed uploaded file via Gemini"
        embed = discord.Embed(
            title=spy_title,
            description=spy_desc,
            color=0xE74C3C,
        )
        embed.add_field(name="🔀 Top Angles", value=angle_summary, inline=False)
        embed.add_field(name="🎯 Top Hook Types", value=hook_summary, inline=False)
        if keyword:
            embed.add_field(name="📊 Source", value=source_val.title(), inline=True)
        embed.add_field(name="🔢 Ads Analyzed", value=str(ads_analyzed), inline=True)
        embed.set_footer(text="AutoResearchClaw v2.5 · Gemini Video Intelligence · Full landscape posted above ↑")

        await interaction.edit_original_response(content=None, embed=embed)

    except Exception as e:
        log.error(f"/competitor-spy error: {e}")
        try:
            await interaction.edit_original_response(content=f"❌ Competitor spy error: {str(e)[:300]}")
        except Exception:
            await interaction.followup.send(f"❌ Competitor spy error: {str(e)[:300]}")
    finally:
        if local_temp and os.path.exists(local_temp):
            os.remove(local_temp)


# ── 19. /provider ─────────────────────────────────────────────────────────────

@bot.tree.command(name="provider", description="Show current LLM provider and model configuration")
async def provider_cmd(interaction: discord.Interaction):
    """Display current provider info from config.yaml."""
    try:
        import sys, pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))
        from agent_runner import get_provider_info
        info = get_provider_info(config=CFG)
    except ImportError:
        await interaction.response.send_message(
            "❌ agent_runner not available — check loop/scripts/agent_runner.py",
            ephemeral=True,
        )
        return

    provider = info["provider"]
    model = info["model"]
    base_url = info["base_url"] or "*(default endpoint)*"
    agent_mode = info["agent_mode"]
    agent_mode_str = "✅ Agent Mode (CLI subprocess)" if agent_mode else "❌ API call"

    # Map provider names to friendly display names
    provider_display = {
        "anthropic": "Anthropic (Claude / Kimi-compat)",
        "minimax": "MiniMax Kimi (Anthropic-compat)",
        "openai": "OpenAI",
        "codex": "Codex CLI 🤖",
        "claude-code": "Claude Code CLI 🤖",
    }.get(provider, provider)

    embed = discord.Embed(
        title="🔧 LLM Provider Config",
        color=0x5865F2 if not agent_mode else 0x57F287,
    )
    embed.add_field(name="Provider", value=provider_display, inline=True)
    embed.add_field(name="Model", value=f"`{model}`", inline=True)
    embed.add_field(name="Base URL", value=f"`{base_url}`", inline=False)
    embed.add_field(name="Agent Mode", value=agent_mode_str, inline=False)
    embed.set_footer(text="AutoResearchClaw v2.6 · Change provider in loop/config/config.yaml → llm.provider")

    await interaction.response.send_message(embed=embed)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        log.error("No bot token configured. Set discord.bot_token in config.yaml or DISCORD_BOT_TOKEN env var.")
        sys.exit(1)
    log.info("Starting AutoResearchClaw Discord bot...")
    bot.run(BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()

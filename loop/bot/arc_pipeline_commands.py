"""
arc_pipeline_commands.py — Discord slash commands for the 23-stage ResearchClaw pipeline.

Commands:
  /arc-run     — Start a new pipeline run
  /arc-status  — Show stage-by-stage status of a run
  /arc-approve — Approve a gate stage (5, 9, 20) to continue
  /arc-reject  — Reject a gate stage → trigger rollback
  /arc-pause   — Pause a running pipeline
  /arc-resume  — Resume a paused pipeline
  /arc-stage   — Jump to a specific stage (expert mode)
  /arc-logs    — Show recent log output for a run

Invoked from discord_bot.py via:
    from arc_pipeline_commands import register_arc_commands
    register_arc_commands(bot)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands

# ── Paths ────────────────────────────────────────────────────────────────────

# Repo root is two levels up from this file (loop/bot/arc_pipeline_commands.py)
REPO_ROOT = Path(__file__).parent.parent.parent
ARTIFACTS_DIR = REPO_ROOT / "artifacts"
CONFIG_DEFAULT = REPO_ROOT / "config.arc.ads.yaml"  # fallback config
VENV_PYTHON = REPO_ROOT / "venv" / "bin" / "python3"

PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

# ── Stage metadata ────────────────────────────────────────────────────────────

STAGE_NAMES: dict[int, str] = {
    1:  "TOPIC_INIT",
    2:  "PROBLEM_DECOMPOSE",
    3:  "SEARCH_STRATEGY",
    4:  "LITERATURE_COLLECT",
    5:  "LITERATURE_SCREEN",       # ⛩ GATE
    6:  "KNOWLEDGE_EXTRACT",
    7:  "SYNTHESIS",
    8:  "HYPOTHESIS_GEN",
    9:  "EXPERIMENT_DESIGN",       # ⛩ GATE
    10: "CODE_GENERATION",
    11: "RESOURCE_PLANNING",
    12: "EXPERIMENT_RUN",
    13: "ITERATIVE_REFINE",
    14: "RESULT_ANALYSIS",
    15: "RESEARCH_DECISION",
    16: "PAPER_OUTLINE",
    17: "PAPER_DRAFT",
    18: "PEER_REVIEW",
    19: "PAPER_REVISION",
    20: "QUALITY_GATE",            # ⛩ GATE
    21: "KNOWLEDGE_ARCHIVE",
    22: "EXPORT_PUBLISH",
    23: "CITATION_VERIFY",
}

GATE_STAGES = {5, 9, 20}

PHASE_LABELS: dict[int, str] = {
    **{s: "A: Research Scoping"    for s in [1, 2]},
    **{s: "B: Literature Discovery" for s in [3, 4, 5, 6]},
    **{s: "C: Knowledge Synthesis"  for s in [7, 8]},
    **{s: "D: Experiment Design"    for s in [9, 10, 11]},
    **{s: "E: Experiment Execution" for s in [12, 13]},
    **{s: "F: Analysis & Decision"  for s in [14, 15]},
    **{s: "G: Paper Writing"        for s in [16, 17, 18, 19]},
    **{s: "H: Finalization"         for s in [20, 21, 22, 23]},
}

# ── In-memory run registry (run_id → pid, config path) ───────────────────────
# NOTE: This survives bot restarts only if we persist to a JSON file.
_REGISTRY_PATH = ARTIFACTS_DIR / ".run_registry.json"


def _load_registry() -> dict[str, dict]:
    if _REGISTRY_PATH.exists():
        try:
            return json.loads(_REGISTRY_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_registry(reg: dict[str, dict]) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(json.dumps(reg, indent=2))


def _register_run(run_id: str, pid: int, run_dir: str, config: str) -> None:
    reg = _load_registry()
    reg[run_id] = {"pid": pid, "run_dir": run_dir, "config": config}
    _save_registry(reg)


def _get_run(run_id: str) -> dict | None:
    return _load_registry().get(run_id)


def _list_runs() -> list[str]:
    """Return run IDs that have an artifacts directory, most recent first."""
    if not ARTIFACTS_DIR.exists():
        return []
    dirs = sorted(
        [d for d in ARTIFACTS_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    return [d.name for d in dirs]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        return {}


def _read_checkpoint(run_dir: Path) -> dict:
    return _read_json(run_dir / "checkpoint.json")


def _read_heartbeat(run_dir: Path) -> dict:
    return _read_json(run_dir / "heartbeat.json")


def _read_summary(run_dir: Path) -> dict:
    return _read_json(run_dir / "pipeline_summary.json")


def _run_is_alive(run_id: str) -> bool:
    """Check if the pipeline process is still running."""
    info = _get_run(run_id)
    if not info:
        return False
    pid = info.get("pid")
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _status_emoji(stage_num: int, current: int, done_stages: set[int]) -> str:
    if stage_num in done_stages:
        return "✅"
    if stage_num == current:
        return "🔄"
    if stage_num in GATE_STAGES:
        return "⛩"
    return "⬜"


def _build_status_embed(run_id: str, run_dir: Path) -> discord.Embed:
    cp = _read_checkpoint(run_dir)
    hb = _read_heartbeat(run_dir)
    summary = _read_summary(run_dir)

    last_done = cp.get("last_completed_stage", 0)
    current_stage = hb.get("last_stage", last_done)
    done_set: set[int] = set(range(1, last_done + 1))

    alive = _run_is_alive(run_id)
    status_label = "🟢 Running" if alive else ("✅ Complete" if last_done == 23 else "🔴 Stopped")

    embed = discord.Embed(
        title=f"🔬 ARC Pipeline — `{run_id}`",
        color=0x5865F2 if alive else (0x57F287 if last_done == 23 else 0xED4245),
    )
    embed.add_field(name="Status", value=status_label, inline=True)
    embed.add_field(name="Progress", value=f"Stage {last_done}/23", inline=True)
    if summary:
        embed.add_field(
            name="Results",
            value=f"✅ {summary.get('stages_done',0)} done · ❌ {summary.get('stages_failed',0)} failed",
            inline=True,
        )

    # Build stage map — show in 3 columns of 8 stages
    lines: list[str] = []
    for num in range(1, 24):
        name = STAGE_NAMES[num]
        emoji = _status_emoji(num, current_stage, done_set)
        gate_tag = " ⛩" if num in GATE_STAGES else ""
        lines.append(f"`{num:02d}` {emoji} {name}{gate_tag}")

    # Split into two fields to fit Discord's 1024 char limit
    embed.add_field(name="Stages 01–12", value="\n".join(lines[:12]), inline=True)
    embed.add_field(name="Stages 13–23", value="\n".join(lines[12:]), inline=True)

    if cp.get("timestamp"):
        embed.set_footer(text=f"Last checkpoint: {cp['timestamp']}")

    return embed


async def _run_pipeline_async(
    run_id: str,
    run_dir: Path,
    config_path: str,
    topic: str,
    from_stage: int = 1,
    auto_approve: bool = False,
) -> subprocess.Popen:
    """Launch pipeline as background subprocess and register it."""
    cmd = [
        PYTHON, "-m", "researchclaw",
        "run",
        "--config", config_path,
        "--output", str(run_dir),
    ]
    if auto_approve:
        cmd.append("--auto-approve")
    if from_stage > 1:
        # Write a checkpoint so the runner resumes from the right stage
        cp = {
            "last_completed_stage": from_stage - 1,
            "last_completed_name": STAGE_NAMES.get(from_stage - 1, ""),
            "run_id": run_id,
            "timestamp": "manual-jump",
        }
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "checkpoint.json").write_text(json.dumps(cp, indent=2))
        cmd += ["--resume"]

    log_path = run_dir / "pipeline.log"
    run_dir.mkdir(parents=True, exist_ok=True)

    log_file = open(log_path, "a")
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=str(REPO_ROOT),
        env={**os.environ, "ARC_TOPIC": topic},
    )
    _register_run(run_id, proc.pid, str(run_dir), config_path)
    return proc


# ── Command registration ──────────────────────────────────────────────────────

def register_arc_commands(bot) -> None:
    """Attach all /arc-* commands to the bot's app_commands tree."""

    # ── Choices ───────────────────────────────────────────────────────────────

    CONFIG_CHOICES = [
        app_commands.Choice(name="ads (Meta/TikTok/Native)", value="config.arc.ads.yaml"),
        app_commands.Choice(name="ads — YouTube",            value="config.arc.ads.youtube.yaml"),
        app_commands.Choice(name="Anthropic (Claude)",       value="config.arc.anthropic.yaml"),
        app_commands.Choice(name="OpenRouter",               value="config.arc.openrouter.yaml"),
        app_commands.Choice(name="Gemini CLI",               value="config.arc.gemini-cli.yaml"),
        app_commands.Choice(name="Codex CLI",                value="config.arc.codex-cli.yaml"),
        app_commands.Choice(name="Claude CLI",               value="config.arc.claude-cli.yaml"),
    ]

    STAGE_CHOICES = [
        app_commands.Choice(name=f"{num:02d} · {name}", value=num)
        for num, name in STAGE_NAMES.items()
    ]

    MODE_CHOICES = [
        app_commands.Choice(name="🚀 Full auto — run all 23 stages, no stops", value="auto"),
        app_commands.Choice(name="⛩  Manual gates — pause at stages 5, 9, 20 for approval", value="manual"),
    ]

    # ── /arc-run ──────────────────────────────────────────────────────────────

    @bot.tree.command(name="arc-run", description="Start a new 23-stage ResearchClaw pipeline run")
    @app_commands.describe(
        topic="Research topic or question",
        mode="Full auto (no stops) or manual gate approval at stages 5, 9, 20",
        config="LLM config to use",
    )
    @app_commands.choices(config=CONFIG_CHOICES, mode=MODE_CHOICES)
    async def arc_run(
        interaction: discord.Interaction,
        topic: str,
        mode: str = "auto",
        config: str = "config.arc.ads.yaml",
    ):
        auto_approve = (mode == "auto")
        await interaction.response.send_message(
            f"🔬 Starting ARC pipeline for **{topic[:80]}**…"
        )

        # Generate run ID from topic slug
        slug = re.sub(r"[^a-z0-9]+", "-", topic.lower())[:30].strip("-")
        import time
        run_id = f"{slug}-{int(time.time())}"
        run_dir = ARTIFACTS_DIR / run_id
        config_path = str(REPO_ROOT / config)

        if not Path(config_path).exists():
            await interaction.edit_original_response(
                content=f"❌ Config `{config}` not found in repo root."
            )
            return

        try:
            proc = await _run_pipeline_async(
                run_id=run_id,
                run_dir=run_dir,
                config_path=config_path,
                topic=topic,
                auto_approve=auto_approve,
            )
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ Failed to launch pipeline: {e}")
            return

        embed = discord.Embed(
            title="🔬 Pipeline Started",
            description=f"**Topic:** {topic[:200]}",
            color=0x5865F2,
        )
        embed.add_field(name="Run ID", value=f"`{run_id}`", inline=True)
        embed.add_field(name="Config", value=config, inline=True)
        embed.add_field(name="PID", value=str(proc.pid), inline=True)
        embed.add_field(
            name="Mode",
            value="🚀 Full auto — runs all 23 stages uninterrupted" if auto_approve else "⛩ Manual gates — will pause at stages 5, 9, 20",
            inline=False,
        )
        embed.add_field(
            name="Next steps",
            value=f"`/arc-status {run_id}` to check progress\n`/arc-logs {run_id}` for live output",
            inline=False,
        )
        embed.set_footer(text="AutoResearchClaw v2.4.0 · 23-stage pipeline")
        await interaction.edit_original_response(content=None, embed=embed)

    # ── /arc-status ───────────────────────────────────────────────────────────

    @bot.tree.command(name="arc-status", description="Show stage-by-stage status of a pipeline run")
    @app_commands.describe(run_id="Run ID (leave blank for most recent run)")
    async def arc_status(interaction: discord.Interaction, run_id: Optional[str] = None):
        await interaction.response.defer()

        if run_id is None:
            runs = _list_runs()
            if not runs:
                await interaction.followup.send("❌ No pipeline runs found in `artifacts/`.")
                return
            run_id = runs[0]

        run_dir = ARTIFACTS_DIR / run_id
        if not run_dir.exists():
            await interaction.followup.send(f"❌ Run `{run_id}` not found.")
            return

        embed = _build_status_embed(run_id, run_dir)
        await interaction.followup.send(embed=embed)

    # ── /arc-approve ──────────────────────────────────────────────────────────

    @bot.tree.command(name="arc-approve", description="Approve a gate stage to continue the pipeline")
    @app_commands.describe(run_id="Run ID to approve", note="Optional approval note")
    async def arc_approve(
        interaction: discord.Interaction,
        run_id: str,
        note: Optional[str] = None,
    ):
        await interaction.response.defer()

        run_dir = ARTIFACTS_DIR / run_id
        if not run_dir.exists():
            await interaction.followup.send(f"❌ Run `{run_id}` not found.")
            return

        # Write approval signal file — pipeline executor polls for this
        hb = _read_heartbeat(run_dir)
        gate_stage = hb.get("last_stage", 0)

        if gate_stage not in GATE_STAGES:
            await interaction.followup.send(
                f"⚠️ Stage {gate_stage} (`{STAGE_NAMES.get(gate_stage,'?')}`) is not a gate stage.\n"
                f"Gate stages are: **5** (Literature Screen), **9** (Experiment Design), **20** (Quality Gate)."
            )
            return

        approval = {
            "decision": "approved",
            "stage": gate_stage,
            "stage_name": STAGE_NAMES.get(gate_stage, ""),
            "approver": str(interaction.user),
            "note": note or "",
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        }
        (run_dir / "gate_approval.json").write_text(json.dumps(approval, indent=2))

        embed = discord.Embed(
            title="✅ Gate Approved",
            color=0x57F287,
        )
        embed.add_field(name="Run", value=f"`{run_id}`", inline=True)
        embed.add_field(name="Stage", value=f"{gate_stage} · {STAGE_NAMES.get(gate_stage, '')}", inline=True)
        embed.add_field(name="Approved by", value=str(interaction.user), inline=True)
        if note:
            embed.add_field(name="Note", value=note, inline=False)
        embed.set_footer(text="Pipeline will continue on next heartbeat check (~30s)")
        await interaction.followup.send(embed=embed)

    # ── /arc-reject ───────────────────────────────────────────────────────────

    @bot.tree.command(name="arc-reject", description="Reject a gate stage — triggers rollback")
    @app_commands.describe(run_id="Run ID to reject", reason="Reason for rejection")
    async def arc_reject(
        interaction: discord.Interaction,
        run_id: str,
        reason: Optional[str] = None,
    ):
        await interaction.response.defer()

        run_dir = ARTIFACTS_DIR / run_id
        if not run_dir.exists():
            await interaction.followup.send(f"❌ Run `{run_id}` not found.")
            return

        hb = _read_heartbeat(run_dir)
        gate_stage = hb.get("last_stage", 0)

        ROLLBACK_MAP = {5: 4, 9: 8, 20: 16}  # gate → rollback target stage
        rollback = ROLLBACK_MAP.get(gate_stage)

        rejection = {
            "decision": "rejected",
            "stage": gate_stage,
            "stage_name": STAGE_NAMES.get(gate_stage, ""),
            "rejector": str(interaction.user),
            "reason": reason or "",
            "rollback_to": rollback,
            "rollback_name": STAGE_NAMES.get(rollback, "") if rollback else "",
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        }
        (run_dir / "gate_approval.json").write_text(json.dumps(rejection, indent=2))

        embed = discord.Embed(
            title="❌ Gate Rejected — Rollback Queued",
            color=0xED4245,
        )
        embed.add_field(name="Run", value=f"`{run_id}`", inline=True)
        embed.add_field(name="Rejected stage", value=f"{gate_stage} · {STAGE_NAMES.get(gate_stage, '')}", inline=True)
        embed.add_field(
            name="Rollback target",
            value=f"Stage {rollback} · {STAGE_NAMES.get(rollback, 'N/A')}" if rollback else "N/A",
            inline=True,
        )
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text="Pipeline will roll back on next heartbeat check (~30s)")
        await interaction.followup.send(embed=embed)

    # ── /arc-pause ────────────────────────────────────────────────────────────

    @bot.tree.command(name="arc-pause", description="Pause a running pipeline (sends SIGSTOP)")
    @app_commands.describe(run_id="Run ID to pause")
    async def arc_pause(interaction: discord.Interaction, run_id: str):
        await interaction.response.defer()

        info = _get_run(run_id)
        if not info:
            await interaction.followup.send(f"❌ Run `{run_id}` not in registry.")
            return
        pid = info.get("pid")
        try:
            os.kill(int(pid), signal.SIGSTOP)
            embed = discord.Embed(title="⏸ Pipeline Paused", color=0xF39C12)
            embed.add_field(name="Run", value=f"`{run_id}`", inline=True)
            embed.add_field(name="PID", value=str(pid), inline=True)
            embed.set_footer(text="Use /arc-resume to continue")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to pause (PID {pid}): {e}")

    # ── /arc-resume ───────────────────────────────────────────────────────────

    @bot.tree.command(name="arc-resume", description="Resume a paused pipeline (sends SIGCONT)")
    @app_commands.describe(run_id="Run ID to resume")
    async def arc_resume(interaction: discord.Interaction, run_id: str):
        await interaction.response.defer()

        info = _get_run(run_id)
        if not info:
            await interaction.followup.send(f"❌ Run `{run_id}` not in registry.")
            return
        pid = info.get("pid")
        try:
            os.kill(int(pid), signal.SIGCONT)
            embed = discord.Embed(title="▶️ Pipeline Resumed", color=0x57F287)
            embed.add_field(name="Run", value=f"`{run_id}`", inline=True)
            embed.add_field(name="PID", value=str(pid), inline=True)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to resume (PID {pid}): {e}")

    # ── /arc-stage ────────────────────────────────────────────────────────────

    @bot.tree.command(name="arc-stage", description="Jump to a specific stage in an existing run")
    @app_commands.describe(run_id="Run ID", stage="Stage to jump to")
    @app_commands.choices(stage=STAGE_CHOICES)
    async def arc_stage(
        interaction: discord.Interaction,
        run_id: str,
        stage: int,
    ):
        await interaction.response.defer()

        run_dir = ARTIFACTS_DIR / run_id
        if not run_dir.exists():
            await interaction.followup.send(f"❌ Run `{run_id}` not found.")
            return

        info = _get_run(run_id)
        config_path = info.get("config", str(CONFIG_DEFAULT)) if info else str(CONFIG_DEFAULT)

        # Kill existing process if alive
        if info and info.get("pid"):
            try:
                os.kill(int(info["pid"]), signal.SIGTERM)
                await asyncio.sleep(1)
            except Exception:
                pass

        # Rewrite checkpoint to (stage - 1) so runner starts at requested stage
        cp = {
            "last_completed_stage": stage - 1,
            "last_completed_name": STAGE_NAMES.get(stage - 1, ""),
            "run_id": run_id,
            "timestamp": "manual-jump",
        }
        (run_dir / "checkpoint.json").write_text(json.dumps(cp, indent=2))

        # Read topic from existing heartbeat/config
        hb = _read_heartbeat(run_dir)
        topic = hb.get("topic", run_id)

        try:
            proc = await _run_pipeline_async(
                run_id=run_id,
                run_dir=run_dir,
                config_path=config_path,
                topic=topic,
                from_stage=stage,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to restart at stage {stage}: {e}")
            return

        embed = discord.Embed(title="⏭ Pipeline Jump", color=0x5865F2)
        embed.add_field(name="Run", value=f"`{run_id}`", inline=True)
        embed.add_field(name="Jump to", value=f"Stage {stage} · {STAGE_NAMES[stage]}", inline=True)
        embed.add_field(name="PID", value=str(proc.pid), inline=True)
        embed.set_footer(text="Pipeline restarted from checkpoint")
        await interaction.followup.send(embed=embed)

    # ── /arc-logs ─────────────────────────────────────────────────────────────

    @bot.tree.command(name="arc-logs", description="Show recent pipeline log output for a run")
    @app_commands.describe(run_id="Run ID (leave blank for most recent)", lines="Number of log lines to show (default 30)")
    async def arc_logs(
        interaction: discord.Interaction,
        run_id: Optional[str] = None,
        lines: int = 30,
    ):
        await interaction.response.defer()

        if run_id is None:
            runs = _list_runs()
            if not runs:
                await interaction.followup.send("❌ No pipeline runs found.")
                return
            run_id = runs[0]

        run_dir = ARTIFACTS_DIR / run_id
        log_path = run_dir / "pipeline.log"

        if not log_path.exists():
            await interaction.followup.send(
                f"❌ No log file found for `{run_id}` — run may not have started yet."
            )
            return

        # Read last N lines
        try:
            text = log_path.read_text(errors="replace")
            log_lines = text.splitlines()
            tail = "\n".join(log_lines[-min(lines, 50):])
            if not tail.strip():
                tail = "(log is empty)"
        except Exception as e:
            await interaction.followup.send(f"❌ Could not read log: {e}")
            return

        # Truncate to Discord 2000-char code block limit
        if len(tail) > 1900:
            tail = "…" + tail[-1900:]

        embed = discord.Embed(
            title=f"📋 Pipeline Log — `{run_id}`",
            description=f"```\n{tail}\n```",
            color=0x2F80ED,
        )
        embed.add_field(name="Lines shown", value=str(min(lines, 50)), inline=True)
        embed.add_field(name="Log file", value=str(log_path.relative_to(REPO_ROOT)), inline=True)
        alive = _run_is_alive(run_id)
        embed.add_field(name="Process", value="🟢 Running" if alive else "🔴 Stopped", inline=True)
        embed.set_footer(text="AutoResearchClaw v2.4.0 · 23-stage pipeline")
        await interaction.followup.send(embed=embed)

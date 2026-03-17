"""
notion_queue_watcher.py — Poll the Notion "Research Run Queue" database for
new rows with Status = "queued", launch the ARC pipeline for each, and update
the row with run state as it progresses.

Usage:
    python3 loop/scripts/notion_queue_watcher.py [--interval 30]

Flow:
    1. Poll DB every --interval seconds
    2. Find rows where Status = "queued"
    3. For each: set Status → running, launch pipeline subprocess
    4. Watch subprocess: update Stage field from heartbeat.json every 10s
    5. On exit: set Status → done or failed, write Run ID and Discord link

Environment / config:
    NOTION_API_KEY  or  config.yaml notion.api_key
    ARC_DISCORD_CHANNEL_ID  — optional, used to build Discord link
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import requests

# ── Paths ─────────────────────────────────────────────────────────────────────

REPO_ROOT    = Path(__file__).parent.parent.parent
ARTIFACTS    = REPO_ROOT / "artifacts"
CFG_PATH     = REPO_ROOT / "loop" / "config" / "config.yaml"
REGISTRY     = ARTIFACTS / ".run_registry.json"
VENV_PYTHON  = REPO_ROOT / "venv" / "bin" / "python3"
PYTHON       = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

# ── Config ────────────────────────────────────────────────────────────────────

DB_QUEUE    = "326bbf40-5fd2-81b6-9f57-f97ef54e7b29"   # Run Queue (new)
DB_BRIEFS   = "325bbf40-5fd2-81f7-be82-d15e47deef30"   # existing hub DBs
DB_HOOKS    = "325bbf40-5fd2-8100-92f1-f92c1b822664"
DB_INTEL    = "325bbf40-5fd2-818f-9eeb-d2e2b11fa62d"
DB_PERSONAS = "325bbf40-5fd2-81e2-bb09-c6354d4433bb"

CONFIG_MAP = {
    "ads (meta/tiktok/native)": "config.arc.ads.yaml",
    "ads — youtube":            "config.arc.ads.youtube.yaml",
    "anthropic (claude)":       "config.arc.anthropic.yaml",
    "openrouter":               "config.arc.openrouter.yaml",
    "gemini cli":               "config.arc.gemini-cli.yaml",
    "codex cli":                "config.arc.codex-cli.yaml",
    "claude cli":               "config.arc.claude-cli.yaml",
}

NOTION_VERSION = "2022-06-28"
NOTION_BASE    = "https://api.notion.com/v1"

DISCORD_CHANNEL_ID = os.getenv("ARC_DISCORD_CHANNEL_ID", "1482759786255745214")

# ── Notion helpers ────────────────────────────────────────────────────────────

def _token() -> str:
    tok = os.getenv("NOTION_API_KEY", "")
    if tok:
        return tok
    try:
        import yaml
        with open(CFG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        tok = cfg.get("notion", {}).get("api_key") or ""
    except Exception:
        pass
    if not tok:
        raise RuntimeError(
            "Notion API key not configured. Set NOTION_API_KEY env var "
            "or notion.api_key in config.yaml"
        )
    return tok


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _query_queued() -> list[dict]:
    """Return all DB rows ready to run: Status = 'queued' OR Status is empty/blank.
    Notion form submissions don't set a default Status, so blank = newly submitted."""
    results = []
    for filter_payload in [
        # Explicit "queued" status
        {"filter": {"property": "Status", "select": {"equals": "queued"}},
         "sorts": [{"timestamp": "created_time", "direction": "ascending"}]},
        # Blank/empty status (form submissions)
        {"filter": {"property": "Status", "select": {"is_empty": True}},
         "sorts": [{"timestamp": "created_time", "direction": "ascending"}]},
    ]:
        r = requests.post(
            f"{NOTION_BASE}/databases/{DB_QUEUE}/query",
            headers=_headers(),
            json=filter_payload,
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[watcher] Notion query failed: {r.status_code} {r.text[:200]}")
            continue
        rows = r.json().get("results", [])
        # Exclude rows already being processed (status = running/done/failed)
        for row in rows:
            status_prop = row.get("properties", {}).get("Status", {})
            current = (status_prop.get("select") or {}).get("name", "")
            if current.lower() not in ("running", "done", "failed"):
                results.append(row)
    return results


def _update_row(page_id: str, **props) -> None:
    """Patch Notion page properties. Supported keys:
       status (str), run_id (str), stage (int), notes (str), discord_link (str)
    """
    properties: dict = {}

    if "status" in props:
        properties["Status"] = {"select": {"name": props["status"]}}
    if "run_id" in props:
        properties["Run ID"] = {"rich_text": [{"type": "text", "text": {"content": str(props["run_id"])[:2000]}}]}
    if "stage" in props:
        properties["Stage"] = {"number": int(props["stage"])}
    if "notes" in props:
        properties["Notes"] = {"rich_text": [{"type": "text", "text": {"content": str(props["notes"])[:2000]}}]}
    if "discord_link" in props:
        properties["Discord Link"] = {"url": str(props["discord_link"])}

    if not properties:
        return

    r = requests.patch(
        f"{NOTION_BASE}/pages/{page_id}",
        headers=_headers(),
        json={"properties": properties},
        timeout=15,
    )
    if r.status_code not in (200, 204):
        print(f"[watcher] Notion update failed: {r.status_code} {r.text[:200]}")


def _extract(page: dict, prop: str) -> str:
    """Extract plain text from a Notion property."""
    p = page.get("properties", {}).get(prop, {})
    ptype = p.get("type", "")
    if ptype == "title":
        parts = p.get("title", [])
    elif ptype == "rich_text":
        parts = p.get("rich_text", [])
    elif ptype == "select":
        sel = p.get("select") or {}
        return sel.get("name", "")
    elif ptype == "number":
        return str(p.get("number", ""))
    else:
        return ""
    return "".join(chunk.get("plain_text", "") for chunk in parts).strip()

# ── Notion content writers (existing hub DBs) ─────────────────────────────────

def _rich(content: str) -> list:
    """Split content into Notion paragraph blocks (2000-char chunks max)."""
    blocks = []
    for i in range(0, min(len(content), 98000), 2000):
        chunk = content[i:i+2000]
        blocks.append({
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]},
        })
    return blocks[:100]


def _create_notion_page(db_id: str, properties: dict, body: str = "") -> Optional[str]:
    payload: dict = {"parent": {"database_id": db_id}, "properties": properties}
    if body:
        payload["children"] = _rich(body)
    r = requests.post(f"{NOTION_BASE}/pages", headers=_headers(), json=payload, timeout=20)
    if r.status_code == 200:
        url = r.json().get("url", "")
        print(f"[notion] Page created: {url}")
        return url
    print(f"[notion] Failed to create page ({db_id}): {r.status_code} {r.text[:200]}")
    return None


def _txt(s: str) -> list:
    return [{"type": "text", "text": {"content": s[:2000]}}]


def _sync_milestone(run_id: str, run_dir: Path, stage: int, topic: str) -> None:
    """Push pipeline artifacts to the existing hub Notion DBs at key stages."""

    # Stage 7 — SYNTHESIS → Briefs DB (synthesis.md)
    if stage == 7:
        f = run_dir / "synthesis.md"
        if f.exists():
            content = f.read_text(errors="replace")
            _create_notion_page(DB_BRIEFS, {
                "Name":     {"title": _txt(f"[ARC] Synthesis — {topic[:80]}")},
                "Status":   {"select": {"name": "Draft"}},
                "Platform": {"select": {"name": "Research"}},
                "Vertical": {"rich_text": _txt(topic[:500])},
            }, content)

    # Stage 8 — HYPOTHESIS_GEN → Briefs DB (hypotheses.md)
    elif stage == 8:
        f = run_dir / "hypotheses.md"
        if f.exists():
            content = f.read_text(errors="replace")
            _create_notion_page(DB_BRIEFS, {
                "Name":     {"title": _txt(f"[ARC] Hypotheses — {topic[:80]}")},
                "Status":   {"select": {"name": "Draft"}},
                "Platform": {"select": {"name": "Research"}},
                "Vertical": {"rich_text": _txt(topic[:500])},
            }, content)

    # Stage 5 — LITERATURE_SCREEN → Intel DB (shortlist.jsonl headlines)
    elif stage == 5:
        f = run_dir / "shortlist.jsonl"
        if f.exists():
            lines = f.read_text(errors="replace").strip().splitlines()
            for line in lines[:20]:  # max 20 intel rows
                try:
                    item = json.loads(line)
                    _create_notion_page(DB_INTEL, {
                        "Headline": {"title": _txt(item.get("title", item.get("url", "Untitled"))[:200])},
                        "URL":      {"url": item.get("url", "")[:2000] or None},
                        "Source":   {"select": {"name": "ResearchClaw"}},
                        "Summary":  {"rich_text": _txt(item.get("abstract", item.get("summary", ""))[:2000])},
                        "Vertical": {"rich_text": _txt(topic[:500])},
                    })
                except Exception:
                    continue

    # Stage 17 — PAPER_DRAFT → Briefs DB (paper_draft.md) + extract hook-like sentences → Hooks DB
    elif stage == 17:
        f = run_dir / "paper_draft.md"
        if f.exists():
            content = f.read_text(errors="replace")
            # Full draft → Briefs
            _create_notion_page(DB_BRIEFS, {
                "Name":     {"title": _txt(f"[ARC] Paper Draft — {topic[:80]}")},
                "Status":   {"select": {"name": "Review"}},
                "Platform": {"select": {"name": "Research"}},
                "Vertical": {"rich_text": _txt(topic[:500])},
            }, content)
            # Extract first sentence of each section as "hooks"
            import re as _re
            hooks = _re.findall(r"^#{1,3} .+\n+(.+)", content, _re.MULTILINE)
            for hook_text in hooks[:15]:
                hook_text = hook_text.strip()
                if len(hook_text) < 20:
                    continue
                _create_notion_page(DB_HOOKS, {
                    "Hook":     {"title": _txt(hook_text[:200])},
                    "Type":     {"select": {"name": "Research Insight"}},
                    "Platform": {"select": {"name": "Research"}},
                    "Vertical": {"rich_text": _txt(topic[:500])},
                })

    # Stage 22 — EXPORT_PUBLISH → Briefs DB (paper_final.md)
    elif stage == 22:
        for fname in ("paper_final.md", "paper_revised.md", "paper_draft.md"):
            f = run_dir / fname
            if f.exists():
                content = f.read_text(errors="replace")
                _create_notion_page(DB_BRIEFS, {
                    "Name":     {"title": _txt(f"[ARC] Final Paper — {topic[:80]}")},
                    "Status":   {"select": {"name": "Approved"}},
                    "Platform": {"select": {"name": "Research"}},
                    "Vertical": {"rich_text": _txt(topic[:500])},
                }, content)
                break


# Track which milestones have already been synced per run
_synced_milestones: dict[str, set[int]] = {}
MILESTONE_STAGES = {5, 7, 8, 17, 22}


# ── Pipeline helpers ──────────────────────────────────────────────────────────

def _load_registry() -> dict:
    if REGISTRY.exists():
        try:
            return json.loads(REGISTRY.read_text())
        except Exception:
            pass
    return {}


def _save_registry(reg: dict) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=2))


def _read_heartbeat(run_dir: Path) -> dict:
    p = run_dir / "heartbeat.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _launch(topic: str, config_file: str, auto_approve: bool) -> tuple[str, Path, subprocess.Popen]:
    """Start the pipeline subprocess. Returns (run_id, run_dir, proc)."""
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower())[:30].strip("-")
    run_id = f"{slug}-{int(time.time())}"
    run_dir = ARTIFACTS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    config_path = str(REPO_ROOT / config_file)
    cmd = [
        PYTHON, "-m", "researchclaw",
        "run",
        "--topic", topic,
        "--config", config_path,
        "--output", str(run_dir),
    ]
    if auto_approve:
        cmd.append("--auto-approve")

    log_file = open(run_dir / "pipeline.log", "a")
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=str(REPO_ROOT),
        env={**os.environ, "ARC_TOPIC": topic},
    )

    # Save to shared registry so Discord commands can also see the run
    reg = _load_registry()
    reg[run_id] = {"pid": proc.pid, "run_dir": str(run_dir), "config": config_path}
    _save_registry(reg)

    return run_id, run_dir, proc

# ── Main watcher loop ─────────────────────────────────────────────────────────

def watch(interval: int = 30) -> None:
    print(f"[watcher] Starting — polling every {interval}s (DB: {DB_QUEUE})")
    active: dict[str, dict] = {}  # page_id → {proc, run_dir, run_id, topic}

    while True:
        # ── Check active runs ──────────────────────────────────────────────
        for page_id, info in list(active.items()):
            proc: subprocess.Popen = info["proc"]
            run_dir: Path = info["run_dir"]
            run_id: str = info["run_id"]
            topic: str = info.get("topic", run_id)

            hb = _read_heartbeat(run_dir)
            stage = hb.get("last_stage", 0)
            if stage:
                _update_row(page_id, stage=stage)

            # Sync milestone stages to existing hub Notion DBs (once each)
            if stage and stage in MILESTONE_STAGES:
                synced = _synced_milestones.setdefault(run_id, set())
                if stage not in synced:
                    try:
                        _sync_milestone(run_id, run_dir, stage, topic)
                        synced.add(stage)
                        print(f"[notion] Synced milestone stage {stage} for {run_id}")
                    except Exception as e:
                        print(f"[notion] Milestone sync error (stage {stage}): {e}")

            ret = proc.poll()
            if ret is not None:
                status = "done" if ret == 0 else "failed"
                notes = "Completed all stages" if ret == 0 else f"Exit code {ret}"
                discord_link = f"https://discord.com/channels/1482209550688981014/{DISCORD_CHANNEL_ID}"
                _update_row(page_id, status=status, stage=stage or 23, notes=notes, discord_link=discord_link)
                # Final sync — push paper_final if stage 22 wasn't caught mid-run
                if ret == 0:
                    synced = _synced_milestones.setdefault(run_id, set())
                    if 22 not in synced:
                        try:
                            _sync_milestone(run_id, run_dir, 22, topic)
                        except Exception:
                            pass
                print(f"[watcher] Run {run_id} finished → {status}")
                del active[page_id]

        # ── Poll for new queued rows ───────────────────────────────────────
        try:
            rows = _query_queued()
        except Exception as e:
            print(f"[watcher] Poll error: {e}")
            time.sleep(interval)
            continue

        for row in rows:
            page_id = row["id"]
            if page_id in active:
                continue  # already launched

            topic = _extract(row, "Topic")
            llm_label = _extract(row, "LLM Config").lower()
            mode = _extract(row, "Mode").lower()

            if not topic:
                print(f"[watcher] Row {page_id} has no topic — skipping")
                continue

            config_file = CONFIG_MAP.get(llm_label, "config.arc.ads.yaml")
            auto_approve = "auto" in mode  # "🚀 Full auto" → True

            print(f"[watcher] Launching: '{topic}' | config={config_file} | auto={auto_approve}")
            _update_row(page_id, status="running", notes=f"Launching…")

            try:
                run_id, run_dir, proc = _launch(topic, config_file, auto_approve)
                _update_row(page_id, status="running", run_id=run_id, stage=1)
                active[page_id] = {"proc": proc, "run_dir": run_dir, "run_id": run_id, "topic": topic}
                print(f"[watcher] Launched {run_id} (PID {proc.pid})")
            except Exception as e:
                _update_row(page_id, status="failed", notes=str(e)[:500])
                print(f"[watcher] Launch failed: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Poll Notion queue and launch ARC pipeline runs")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds (default: 30)")
    args = parser.parse_args()

    try:
        watch(interval=args.interval)
    except KeyboardInterrupt:
        print("\n[watcher] Stopped.")

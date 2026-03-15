---
name: auto-research-claw
description: >
  Run AutoResearchClaw — a fully autonomous research pipeline that goes from a
  topic prompt to a structured research output. Use when someone says "research X",
  "run a brief on X", "brief me on X for [platform]", or needs deep research on
  any topic: ad creative strategy, market research, competitive intel, audience
  analysis, product categories, verticals, or academic/ML research.
  Works for any domain. Platform configs (meta/tiktok/youtube/native) add
  ad-creative framing; without them it produces general research output.
metadata:
  openclaw:
    requires:
      bins: []
      python: "3.11"
    install:
      - id: pip
        kind: shell
        label: "Install AutoResearchClaw dependencies"
        command: |
          cd ~/.openclaw/workspace/AutoResearchClaw
          python3 -m venv .venv
          .venv/bin/pip install -e . -q
---

# AutoResearchClaw

Fully autonomous research: **Chat an idea → Get a paper.**

Runs a 23-stage pipeline: topic scoping → literature search → experiment design → code execution → paper writing → citation verification.

Uses **Codex CLI** (authenticated via your ChatGPT Pro/Plus account — no separate OpenAI API key needed).

---

## Setup (first time only)

```bash
# Clone into your workspace
git clone https://github.com/ArielleTolome/AutoResearchClaw.git \
  ~/.openclaw/workspace/AutoResearchClaw

cd ~/.openclaw/workspace/AutoResearchClaw

# Create virtualenv and install
python3 -m venv .venv
.venv/bin/pip install -e .

# Copy the codex-cli config
cp config.arc.codex-cli.yaml config.arc.yaml
```

Codex CLI auth uses `~/.codex/` — run `codex login` once if not already authenticated.

---

## Running Research

### Via shell script (recommended)

```bash
cd ~/.openclaw/workspace/AutoResearchClaw
./research.sh "Your research topic here"
```

For manual gate review (stops at approval checkpoints):
```bash
./research.sh "Your topic" --no-auto-approve
```

With a custom config:
```bash
./research.sh "Your topic" --config config.arc.codex-cli.yaml
```

### Via Python CLI

```bash
cd ~/.openclaw/workspace/AutoResearchClaw
.venv/bin/researchclaw run \
  --config config.arc.codex-cli.yaml \
  --topic "Your research topic here" \
  --auto-approve
```

---

## Codex CLI — Correct Invocation

The pipeline internally uses `codex exec` with these flags:

```bash
codex exec --json --skip-git-repo-check -o <outfile> -
```

> **Do NOT use these — they don't exist in Codex CLI:**
> - `--approval-policy never` ❌
> - `-q "prompt"` ❌
> - `-m o4-mini` ❌ (use `-c model=o4-mini` instead)

For quick one-off research prompts (outside the full pipeline):

```bash
/path/to/codex exec -s danger-full-access "Your research prompt here"
```

---

## Configuration

Edit `config.arc.yaml` before running. Key fields:

```yaml
research:
  topic: "Override the topic here (or pass via --topic flag)"
  daily_paper_count: 10        # papers fetched from arXiv/Semantic Scholar
  quality_threshold: 4.0       # 0-5 gate score to advance stages

llm:
  provider: "codex-cli"        # uses ChatGPT Pro via Codex CLI
  primary_model: "gpt-5.3-codex-spark"  # default model

experiment:
  mode: "sandbox"              # sandbox | ssh_remote
  # For GPU jobs on a remote machine:
  ssh_remote:
    host: "100.86.239.1"       # Bill's GPU server
    remote_workdir: "/tmp/researchclaw_experiments"
```

---

## Output Artifacts

After a run, results land in `artifacts/rc-<timestamp>/`:

| File | Description |
|------|-------------|
| `stage-22*/paper_final.md` | Final research paper (Markdown) |
| `stage-22*/paper_final_latex.md` | LaTeX version (if generated) |
| `stage-12*/runs/results.json` | Experiment results + metrics |
| `stage-14*/charts/` | Result charts (PNG) |
| `stage-14*/analysis.md` | Multi-perspective analysis |
| `stage-08*/hypotheses.md` | Generated research hypotheses |
| `pipeline_summary.json` | Full stage-by-stage summary |
| `checkpoint.json` | Last completed stage (resume point) |

---

## Prompt Modes (Optional Overrides)

AutoResearchClaw ships with alternative prompt sets that swap the pipeline's output format without touching the engine.

**Default mode** — produces ML research papers (academic tone, NeurIPS/ICML structure, citations, ablations). This is what runs unless you explicitly opt in to something else.

### Ad Creative Mode (`prompts.ads.yaml`)

Reorients all 23 stages toward **performance ad creative strategy**. Same pipeline, different output.

**Output:** creative brief with persona card, 3 angles, 20+ hooks, 3 full ad concepts, competitive gap map, testing plan, copy swipe file.

**To enable** (Rachel / creative agents only):

```yaml
# config.arc.yaml
prompts:
  custom_file: "prompts.ads.yaml"
```

**Do NOT set this** unless you want creative briefs instead of research papers. All other agents should leave `prompts:` unset or remove the `custom_file` key entirely.

---

## Discord Trigger (Rachel)

Rachel can run a brief for you directly from Discord — no CLI needed.

**Just say:**
> "Run a brief on [topic] for [platform]"
> e.g. "Do national debt relief for Facebook ads"

Rachel fires `run_brief.sh` on Bill's GPU server, sets a poll every 5 min, and posts the full creative brief back to Discord when done (~30 min).

### run_brief.sh

Located at the repo root. Usage:

```bash
bash run_brief.sh "<topic>" [platform] [run_id]
```

Platforms: `meta` (default) | `tiktok` | `youtube` | `native`

- Picks the right platform config automatically
- Patches topic into a temp YAML (original configs untouched)
- Runs `research.sh` in the background via `nohup`
- Drops `.done` / `.fail` markers in `/root/arc_runs/` when finished
- Prints `<run-id>` and `PID` so Rachel can poll

**Poll a run:**

```bash
# Status
ls /root/arc_runs/<run-id>.done 2>/dev/null && echo DONE \
  || ls /root/arc_runs/<run-id>.fail 2>/dev/null && echo FAILED \
  || echo RUNNING

# Live log
tail -50 /root/arc_runs/<run-id>.log

# Output files
find /root/AutoResearchClaw/runs/<run-id> -name '*.md' 2>/dev/null
```

**Output:**

```
runs/<run-id>/stage-23/creative_brief.md   ← angles, hooks, ad concepts
runs/<run-id>/stage-23/hooks.md            ← full hook matrix
runs/<run-id>/stage-04/candidates.jsonl    ← raw audience language
```

---

## Tips

- Keep topics **specific** — "loyalty discount mechanics in auto insurance" beats "auto insurance"
- Use `--no-auto-approve` for sensitive domains where you want to review at gates
- For GPU experiments, set `experiment.mode: ssh_remote` and point to Bill's server
- Research outputs should feed into your next creative brief or hook batch
- Tag runs in Discord `#research` so Marcus and Christina can see findings

---

## Creative Loop — Self-Improving Ad Testing (v0.9.6)

The `loop/` directory contains a fully autonomous ad creative testing pipeline.
Inspired by Karpathy's AutoResearch loop — same principle, applied to Meta ads.

**Research pipeline → seeds baseline → loop tests + improves autonomously.**

### The Cycle

```
HARVEST → ANALYZE → GENERATE → DEPLOY → 48h → repeat
```

### Quick Start

```bash
cd ~/.openclaw/workspace/AutoResearchClaw/loop
pip install -r requirements.txt
cp config/config.yaml.example config/config.yaml
# Fill in: meta.access_token, meta.ad_account_id, llm.api_key, offer.*

# Dry run
python orchestrator/orchestrator.py --dry-run

# Live run
python orchestrator/orchestrator.py --adset-id act_XXXXXXXX --link-url https://yourpage.com
```

### Rachel Trigger (from Discord)

> "Start the loop for [offer] on adset [ID]"
> "Run a loop cycle dry run"
> "Check loop learnings"

Rachel SSHes into Bill's server and runs the orchestrator.

### Key Files

| File | Purpose |
|------|---------|
| `loop/config/config.yaml` | API keys, offer config, kill rules |
| `loop/config/baseline.md` | Seed this from research brief output |
| `loop/learnings/learnings.md` | Compounding memory — grows each cycle |
| `loop/config/prompts.yaml` | System prompts for analyze/generate/consolidate |

### Loop via GitHub Actions

Push to `ArielleTolome/AutoResearchClaw` with GitHub Secrets set.
Actions cron: every 48h. Also triggerable manually from Actions UI.

### Kill Rules (defaults)

- Hook rate < 15% → pause
- CTR < 0.5% → pause  
- CPA > 3x target CPA → pause (Marcel's rule)

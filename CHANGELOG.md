# AutoResearchClaw Changelog

## v0.9.6 — 2026-03-15

### Added
- **Creative Loop** (`loop/`) — self-improving ad creative testing pipeline inspired by Karpathy's AutoResearch loop
  - `loop/orchestrator/orchestrator.py` — main loop runner (full cycle or single step)
  - `loop/scripts/harvest.py` — Meta Marketing API data pull (hook rate, CTR, CVR, CPA)
  - `loop/scripts/analyze.py` — composite scoring, kill rules, Claude qualitative analysis, learnings logging
  - `loop/scripts/generate.py` — challenger creative generation using creative frameworks + learnings
  - `loop/scripts/deploy.py` — Meta ad creation, loser pausing, Slack notifications
  - `loop/config/prompts.yaml` — analyze, generate, and consolidate system prompts
  - `loop/config/config.yaml.example` — full configuration reference
  - `loop/learnings/learnings.md` — compounding memory log (grows each cycle)
  - `.github/workflows/loop.yml` — GitHub Actions cron (every 48h)
- **CHANGELOG.md** — this file

### How it connects to the research pipeline
Research pipeline output (creative brief) → seeds `loop/config/baseline.md` → loop autonomously tests + improves it

---

## v0.9.5 — 2026-03-15

- Live data fetching from Reddit, Amazon reviews, Facebook Ad Library proxy, trending keywords
- Rachel configuration system (config.arc.ads.yaml, config.arc.ads.tiktok.yaml, config.arc.ads.youtube.yaml, config.arc.ads.native.yaml)
- Platform variants for Meta, TikTok, YouTube, Native

## v0.9.4

- prompts.ads.yaml documented as opt-in only — all other agents unaffected

## v0.9.2

- SKILL.md added for OpenClaw/OpenCode integration
- 4 live-run bug fixes (SKILL.md paths, YAML nested fence parse, metric direction, max_iterations)

## v0.9.0

- Initial fork from aiming-lab/AutoResearchClaw
- ACP provider + test fixes merged from upstream

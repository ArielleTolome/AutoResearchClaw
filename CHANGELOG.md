# AutoResearchClaw Changelog

## v1.5.0 — 2026-03-15

### Added
- **Angle Fatigue Scorer** (`loop/scripts/angle_fatigue.py`)
  - Scores every known angle on 4 dimensions: volume (# competitor ads) + diversity (# unique advertisers) + recency (how recently new entrants appeared) + longevity decay (are ads dying faster?)
  - Output tiers: `FRESH` (<25) | `WARMING` (25–50) | `SATURATED` (51–75) | `DEAD` (>75)
  - Covers all 10 ACA angles + 9 additional (geo_personalization, mechanism, loss_aversion, news_tie_in, etc.)
  - Loads intel from Baserow (if configured) or falls back to local intel JSON harvest files
  - Runs as **Step 2.5** — between Analyze and Generate every cycle
  - Fatigue context injected into generate prompt: LLM actively avoids DEAD angles, prefers FRESH/WARMING
  - Standalone: `python angle_fatigue.py` prints full fatigue heatmap for your niche
- **Prediction Scorer** (`loop/scripts/prediction_scorer.py`)
  - Pre-deploy creative scoring on 5 dimensions (25-point rubric, matching ACA Hook Scoring)
  - **Hook Clarity** (LLM) — 3-second clarity test
  - **Tension/Desire** (LLM) — pain agitation OR desire amplification
  - **Angle Freshness** (fatigue data) — FRESH=5, WARMING=3, SATURATED=1, DEAD=0
  - **Awareness Match** (rule-based) — Schwartz stage 1-5 copy alignment
  - **Pattern Interrupt** (LLM) — scroll-stop quality
  - Predicted hook rate output: `22-25→35-45%` | `18-21→25-35%` | `14-17→15-25%` | `<14→iterate`
  - Config: `prediction.min_score_to_deploy` — set to 14 to block weak creatives from reaching gate
  - Runs as **Step 3.3** — after Generate, before Approval Gate
- Discord approval gate embed updated: now includes **prediction score block** + **angle fatigue heatmap** in every gate message
- `generate_challenger()` accepts `fatigue_context` — angle fatigue data now steers LLM away from dead angles
- New config sections: `prediction.*`, `angle_fatigue.*`

### Loop flow (updated)
```
HARVEST → ANALYZE → [2.5 ANGLE FATIGUE] → GENERATE → [3.3 PREDICT] → GATE (with scores) → DEPLOY
```

---

## v1.4.0 — 2026-03-15

### Added
- **Reviews source** (`loop/scripts/sources/reviews_source.py`) — mine competitor brand reviews for raw buyer language
  - Sources: Trustpilot, ConsumerAffairs, BBB, Yelp, SiteJabber (via Tavily — same key, no extra cost)
  - **Why reviews beat Amazon for this use case:** competitor brand reviews = emotional language about the exact pain your offer solves
  - Star-bucketed output: `hook_type: review_1star` (pain/objections) / `review_5star` (desired outcomes) / `review_3star` (nuanced truth)
  - Auto-inferred angle labels: `pricing_pain`, `loyalty_betrayal`, `savings_discovery`, `claim_experience`, `ease_of_use`, `abandonment_fear`
  - Niche-specific query sets for: auto_insurance, dental_implants, weight_loss, medicare, debt_relief, home_insurance, life_insurance
  - Config: `reviews.max_results_per_query`, `reviews.star_filter` ("1"/"5"/"3"/"all")
  - Reuses `tavily.api_key` — no new credentials needed
- Reviews wired into `run_intel_harvest()` as 6th source

### How to use the star buckets
- **1★ reviews** → hooks that agitate pain: "Still waiting 8 months for my claim to be paid"
- **5★ reviews** → hooks that inspire desire: "Switched in 10 minutes and saved $800/year"
- **3★ reviews** → angle refinement: find what's *almost* right about competitors

---

## v1.3.0 — 2026-03-15

### Added
- **Tavily source** (`loop/scripts/sources/tavily_source.py`) — mine Reddit and forum audience language via Tavily Search API
  - No OAuth, no CAPTCHA — zero-friction setup
  - Niche-specific query sets for auto_insurance, dental_implants, weight_loss, medicare, personal_finance
  - Returns raw verbatim audience language: complaints, trigger events, objections, desired outcomes
  - Auto-tagged as `hook_type: audience_language` for the analyze step
  - Config: `tavily.api_key`, `tavily.include_news`, `tavily.max_results_per_query`
- Tavily wired into `run_intel_harvest()` alongside Anstrex, Foreplay, FB Ads Library, YouTube
- Tavily config section added to `config.yaml.example`

---

## v1.2.0 — 2026-03-15

### Added
- **Baserow integration** (`loop/scripts/baserow_sink.py`) — permanent structured intel memory layer
  - `write_intel_ads()` — all competitor ads from every harvest source
  - `write_hooks()` — extracted hooks with scores
  - `write_creative_brief()` / `update_brief_status()` — briefs with status lifecycle (pending → approved → deployed)
  - `write_loop_result()` — each 48h cycle: CPA before/after, delta %, challenger copy, learnings summary
  - JWT auth with graceful degradation (failures log + skip, never crash pipeline)
- 4 pre-created Baserow tables: `intel_ads` (813), `hooks` (814), `creative_briefs` (815), `loop_results` (816)
- Config: `baserow.enabled`, `baserow.email`, `baserow.password`

---

## v1.1.2 — 2026-03-15

### Fixed
- Anstrex source: use `title` param for keyword filtering (`keyword` param silently ignored by API)

---

## v1.1.1 — 2026-03-15

### Fixed
- Anstrex source: replaced Cloudflare-blocked scraper with real internal app API (`api.anstrex.com`)
  - Bearer token auth, `sort_id=10` for duration desc (longest-running first)
  - `min_running_days` filter via `created_at` delta

---

## v1.1.0 — 2026-03-15

### Added
- **Multi-source intel harvest** — competitive intelligence from 4 live sources:
  - `sources/anstrex_source.py` — native/push/pops ad intel (Taboola, Outbrain, Newsbreak)
  - `sources/fb_ads_library_source.py` — longevity tracking via Meta Ads Archive API
  - `sources/foreplay_source.py` — longest-running Facebook/Instagram creatives
  - `sources/youtube_source.py` — top ad video hooks with transcript extraction
- `sources/__init__.py`, `sources/README.md`
- All sources are best-effort with try/except — one failure never blocks others
- Intel output saved to `loop/learnings/intel_YYYYMMDD_HHMMSS.json`

---

## v1.0.0 — 2026-03-15

### Added
- **Qdrant memory persistence** — cross-campaign learnings stored in `rachel-memories` collection
  - Writes learning summary after each loop cycle
  - Recalls top-K relevant memories at cycle start to improve challenger generation
- **Discord reaction approval gate** — challenger copy posted as embed to `#creative` channel
  - ✅ reaction = approve and deploy, ❌ = kill, ⏭️ = skip cycle
  - Configurable timeout action (kill / deploy / skip)
  - Allowed reactors list (whitelist specific Discord user IDs)
- **Discord embed reporting** — rich cycle summaries with winner/loser metrics, CPA delta, hook scores
- Config: `qdrant.enabled`, `approval_gate.enabled`, `approval_gate.bot_token`

---

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

# AutoResearchClaw Changelog

## [2.7.0] — 2026-03-17

### 🔧 Stability — Python 3.9 Compat + MiniMax Think-Block Fix

#### Bug fixes
- **MiniMax `<think>` block stripping** — `_safe_json_loads` now strips `<think>…</think>` prefixes, markdown fences, and uses regex fallback extraction. Fixes Stage 4 silently producing 0 candidates on every run when using MiniMax M2.5
- **Python 3.9 type union syntax** — Added `from __future__ import annotations` to all 22 `loop/scripts/` files. `str | None`, `dict | None`, `list[X] | None` are 3.10+ syntax; without this every creative command (`/research`, `/full-brief`, `/gen-hooks`, `/score`, etc.) crashed on import
- **DOD checker false failures** — Empty directories and `.jsonl` files no longer cause pipeline abort; graceful fallbacks for stages with 0 candidates
- **FB Ads Library API 400** — `ad_reached_countries` now properly JSON-encoded (`["US"]` not `['US']`)

#### Commands status: 35 slash commands, all importable on Python 3.9+

---

## [2.6.0] — 2026-03-16

### 🤖 Agent Mode — Multi-Provider LLM Support

#### New providers in `action_handler.py` + `_kimi()` helper
- **Codex CLI** — `provider: codex` routes LLM calls through the local Codex CLI
- **Claude Code** — `provider: claude-code` routes through `claude --print` (bypass permissions mode)
- **OpenAI** — `provider: openai` with standard API key config
- **MiniMax** (default) — unchanged, Anthropic-compat endpoint
- `/provider` command — show current LLM provider and model config
- Timeout bumped to 300s for slow providers (Codex, Claude Code)
- `/research` prompt trimmed to prevent timeout on large topics

---

## [2.5.1] — 2026-03-16

### 📎 Discord File Attachment Support

- `/ad-dissect`, `/video-qa`, `/competitor-spy` — accept Discord file attachments (video/image) directly instead of requiring a URL
- Downloads attachment to temp file, passes to Gemini for analysis
- Cleaned up after analysis completes

---

## [2.5.0] — 2026-03-16

### 🎬 Gemini Video Intelligence

#### New scripts
- `loop/scripts/ad_dissect.py` — dissect any ad video with Gemini: hook type, body structure, CTA, persona, production quality, ACA framework breakdown
- `loop/scripts/video_qa.py` — pre-launch QA on video creatives: clarity, hook strength, CTA legibility, brand safety
- `loop/scripts/competitor_video_spy.py` — spy on competitor video ads: angle detection, hook patterns, format analysis

#### New bot commands
- `/ad-dissect [url] [attachment]` — full ACA block dissection of any video ad
- `/video-qa [url] [attachment]` — Gemini QA checklist before launch
- `/competitor-spy [url] [keyword]` — competitor video analysis + angle gap report

---

## [2.4.0] — 2026-03-16

### 🔄 Variation Commands — 8 New Iteration Tools

#### New bot commands (all powered by `_kimi()`)
- `/spin [concept]` — 8 distinct variations of one ad block (hook/body/CTA swaps)
- `/remix [concept_a] [concept_b]` — combine best elements of two concepts into 3 hybrids
- `/awareness-shift [concept] [stage]` — rewrite for a different Schwartz awareness stage (1–5)
- `/format-adapt [concept] [format]` — adapt brief for specific format (UGC/static/VSL/carousel)
- `/persona-swap [concept] [persona]` — rewrite angle for a specific persona
- `/angle-matrix [offer]` — map offer across all 10 ACA angles with hook starters
- `/hook-ladder [hook]` — rewrite one hook across all 17 ACA execution types
- `/fatigue-check [concept]` — evaluate creative fatigue signals + pattern interrupt suggestions

#### Fixes
- `/gen-hooks` embed now shows hook types and scores (was truncated)
- Notion hook titles and scores now render correctly (no raw markdown leaking)
- Provider label in embed footer is dynamic (shows actual model, not hardcoded)

---

## [2.3.0] — 2026-03-16

### 📊 The Batch Machine + ACA Creative Testing Tracker

#### New: `loop/scripts/concept_vault.py`
- Concept lifecycle: `testing → proven → scaling → dead`
- Tracks batch count, best hook rate, winning hook types per concept
- JSON store at `loop/learnings/concept_vault.json`

#### New: `loop/scripts/batch_generator.py`
- Generates 15/45/90/180-ad variation batches
- Combinations: hooks × actors × music × scenes
- 80/20 iteration rule enforced (80% proven iterations, 20% new concepts)

#### New: `loop/scripts/creative_tracker.py`
- Full Baserow table 819 interface — exact replica of ACA Creative Testing Tracker Excel
- Columns: Test Status, Asset Type, Concept ID, Hook, Ad Type, Hypothesis, Action Items, Results, ROAS, CPA, CVR, CTR, Hook Rate, Hold Rate, Avg Watch Time
- Seeded with ACA template examples + Stimulus Assistance concepts

#### New bot commands
- `/batch [concept_id] [size]` — generate ad variation batch (15/45/90/180)
- `/concept-status` — show concept vault (testing/proven/scaling/dead)
- `/tracker [status] [offer]` — view and manage creative testing tracker

---

## [2.2.0] — 2026-03-15

### 🚀 Discord Bot — 8 New Slash Commands

#### New commands in `loop/bot/discord_bot.py`
- `/research [topic] [platform]` — Run full research pipeline (Reddit + News + Competitors), synthesize with Kimi into structured intel report with pain points, angles, and hook opportunities
- `/full-brief [topic] [platform]` — Generate a complete ACA creative brief: awareness stage, persona, 3 angles, 15 hooks, ad concept timeline, platform notes
- `/gen-hooks [topic] [awareness_stage]` — Generate 20 hooks across all 17 ACA hook types (Question, Shocking Stat, Contrarian, Story, etc.) with scores
- `/spy [keyword] [days_running]` — Search Anstrex for native ads + competitor_watcher analysis, identify dominant angles and counter-angle opportunities
- `/daily-intel` — Run the full daily intel pipeline on demand (news → reddit → competitors → digest → signal cards) with parsed result counts
- `/loop-status` — Show creative loop status: offer config, learnings word count, baseline preview (instant, no defer)
- `/score [copy]` — Score ad copy against ACA QA checklist on 7 dimensions (70-point scale), color-coded verdict (LAUNCH/REVISE/KILL)
- `/intel-digest` — Run intel_digest.py and post results as Discord embed

#### New helpers
- `_run_script()` — async subprocess wrapper for running pipeline scripts
- `_kimi()` — async wrapper for Kimi M2.5 LLM calls via Anthropic-compatible API
- `_post_embed()` — chunked embed poster with v2.2 footer

#### Dependencies
- Added `aiohttp` for direct Anstrex API calls in `/spy`

---

## [2.1.0] — 2026-03-15

### 🤖 Discord Bot — One-Click Briefs

#### New: `loop/bot/discord_bot.py`
- Discord bot with persistent button interactions on signal cards
- `/post-signals` slash command: fetches today signal cards JSON, posts as embeds with 5 action buttons
- Button click → action_handler.py runs → result posted back as embed in <5 seconds
- Actions: Brief, Persona, Hooks, Script, Image Concepts
- Powered by Kimi M2.5-highspeed via action_handler.py
- Config: `discord.bot_token` + `discord.signal_channel_id`
- Run: `bash loop/bot/run_bot.sh`

---

## [2.0.1] — 2026-03-15

### LLM Provider — Multi-Model Support

- `action_handler.py` now reads `llm.base_url` from config — point at any Anthropic-compatible endpoint
- Default model switched to **MiniMax Kimi M2.5-highspeed** (`https://api.minimax.io/anthropic`) — ~10x cheaper than Sonnet, same API shape
- API key resolution order: `config.yaml llm.api_key` → `MINIMAX_API_KEY` env → `ANTHROPIC_API_KEY` env
- `config.yaml` updated with commented fallback options for Claude Sonnet and GLM-4-plus
- `daily-intel.yml` both config blocks updated to Kimi + `MINIMAX_API_KEY` secret
- Swap back to Claude anytime: change `model` + remove `base_url` in config

---

## [2.0.0] — 2026-03-15

### 🎯 Intel-to-Brief in One Click

#### New: `action_handler.py`
- **5 creative actions** runnable against any signal: Brief, Persona, Hooks, Script, Image Concepts
- Claude claude-sonnet-4-6 generates each asset with action-specific prompts rooted in ACA doctrine
- Fetches signal from Baserow (tables 767/818/813), calls Anthropic API, posts embed to Discord
- CLI: `python action_handler.py --signal-id <id> --action generate_brief`
- Flags: `--dry-run` (print only), `--output` (save to file), `--webhook-url` (override)

#### Updated: `signal_cards.py`
- New `--post` flag: pushes signal card embeds directly to Discord via webhook
- Each card becomes a color-coded embed (by emotion) with signal context

#### Infrastructure
- Qdrant API key wired in `qdrant_sink.py` — signals now embed into `autoresearch-signals` collection
- `competitor_watcher.py` parser fixed: Anstrex response now correctly read from `data.data` path
- `qdrant.api_key` field added to `loop/config/config.yaml` schema

---

## v2.0.0-alpha — 2026-03-15

### Added
- **Qdrant Memory Layer** (`loop/scripts/qdrant_sink.py`) — signal vectorization and semantic search
  - Collection: `autoresearch-signals` (1536-dim cosine, OpenAI text-embedding-3-small)
  - `upsert_signal()` — embeds "{vertical} {headline} {summary}", upserts with full payload
  - `search_similar()` — semantic search with optional vertical filter
  - `get_signals_since()` — time-range retrieval for trend analysis
  - Dedup by SHA256 hash of source_url or headline+date as point ID
  - `news_watcher.py` and `reddit_source.py` now upsert to Qdrant after every Baserow write (conditional)
- **Trend Momentum Scorer** (`loop/scripts/trend_scorer.py`) — 7-day rolling topic tracking
  - Groups signals by normalized topic using fuzzy string matching (fuzzywuzzy)
  - Momentum tiers: Stable (1 occurrence), Rising 📈 (2-3), Trending 🔥 (4+)
  - Weekly "What's Heating Up" Discord card via `--weekly-card` flag
  - CLI: `--vertical`, `--days`, `--json`, `--weekly-card`
- **Auto Persona Builder** (`loop/scripts/persona_builder.py`) — weekly vertical persona cards
  - Reads Reddit audience signals from Qdrant or Baserow table 818 fallback
  - Sends to Claude with Rachel persona prompt for structured persona card
  - Output: Name, Age Range, Core Pain, Primary Emotion, Trigger Event, Key Verbatim Phrases, Creative Angle, Hook Type
  - Saves to `loop/personas/{vertical}_{date}.md`
  - `--post` flag sends embed to #creative Discord webhook
  - Weekly GHA job: every Monday 10AM UTC for Medicare + Auto Insurance
- **Meta Ad Library Scraper** (`loop/scripts/meta_ad_library.py`) — Facebook/Instagram competitor intel
  - Queries Meta Ad Library API v21.0 (`ads_archive` endpoint) for 5 verticals
  - Extracts hook (first line of ad body), page_name, spend bucket, delivery start date
  - Freshness scoring: Veteran (>30d), Active (7-30d), New (<7d)
  - Posts high-value ads (Veteran + Active) to #competitor-watch Discord channel
  - Handles pagination via after cursor; graceful auth error messages
  - CLI: `--vertical`, `--dry-run`, `--limit`, `--json`
- **Signal Card Builder** (`loop/scripts/signal_cards.py`) — button payload generator for Rachel bot
  - Reads fresh signals from Baserow tables 767 (news) and 818 (reddit)
  - Builds structured card JSON with 5 action buttons: Brief, Persona, Hooks, Script, Image Ad
  - Output saved to `loop/output/signal_cards_{date}.json`
  - Cards consumed by OpenClaw Rachel agent for interactive Discord posting
  - Added to daily GHA pipeline (runs after intel_digest)

### Changed
- **GHA daily-intel.yml** — expanded pipeline:
  - Added `meta_ad_library.py` to daily competitor step
  - Added `signal_cards.py` step after intel_digest
  - Added weekly `persona-builder` job (Monday 10AM UTC)
  - Expanded pip install to include anthropic, openai, qdrant-client, fuzzywuzzy
  - Config template now includes meta, llm, and qdrant sections
- **requirements.txt** — added fuzzywuzzy>=0.18.0 and python-Levenshtein>=0.12.0
- **config.yaml.example** — added `qdrant.signals_collection` field

### New directories
- `loop/output/` — signal card JSON output (with .gitkeep)
- `loop/personas/` — generated persona card markdown files

---

## v1.6.0 — 2026-03-15

### Added
- **signals.py** — 32 typed Discord signal cards with dual-channel routing
  - **Performance signals:** `WINNER`, `FATIGUE`, `KILL`, `SCALE`, `HOOK_RATE_SPIKE`, `CPM_SURGE`, `ZERO_SPEND`, `DAILY_CAP_HIT`
  - **Testing signals:** `TEST_READY`, `VARIANT_DIVERGING`, `ANGLE_EXHAUSTED`, `NEW_ANGLE_IDENTIFIED`
  - **Research signals:** `REDDIT_VERBATIM`, `COMPETITOR_NEW_AD`, `COMPETITOR_KILLED`, `TREND_DETECTED`
  - **Loop health signals:** `LOOP_START`, `LOOP_ERROR`, `BRIEF_COMPLETE`, `CHALLENGER_READY`, `MEMORY_MILESTONE`, `LEARNING_CONSOLIDATION`, `LOOP_STREAK`, `CYCLE_SUMMARY`
  - **Creative signals:** `HOOK_TYPE_RANKING`, `ANGLE_ROTATION_DUE`, `MODULAR_READY`, `AWARENESS_SHIFT`
  - **Platform signals:** `NATIVE_ADVERTORIAL_CTR`, `PUSH_CLICK_ANOMALY`, `PLATFORM_WINNER`
- **Dual-channel routing** — competitor intel (`COMPETITOR_NEW_AD`, `COMPETITOR_KILLED`, `TREND_DETECTED`, `NEW_ANGLE_IDENTIFIED`) routes to `#competitor-watch`; all other signals route to `#creative-signals`
- Each signal card has: typed emoji header, color-coded embed, top metrics, score, recommended action, Rachel footer + timestamp
- `CHALLENGER_READY` cards auto-post ✅/❌ reactions for the approval gate
- CLI test mode: `python signals.py <signal_type>` fires a sample card
- 32 convenience functions exported: `emit_winner()`, `emit_fatigue()`, `emit_competitor_new_ad()`, etc.
- `config.yaml` — new `discord.competitor_webhook_url` + `discord.competitor_channel_id` fields

### Config additions
```yaml
discord:
  webhook_url: "YOUR_WEBHOOK"           # → #creative-signals
  channel_id: "1482849535473614848"
  competitor_webhook_url: "YOUR_WEBHOOK"  # → #competitor-watch
  competitor_channel_id: "1482851982837547048"
```

---

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

# Intel Harvest Sources

Multi-source competitive intelligence layer for the AutoResearchClaw loop.
Each source fetches ad creative data from a different platform and normalizes it
into a common format for analysis.

## Sources

### Foreplay (`foreplay_source.py`)
- **API:** Foreplay Public API (`https://public.api.foreplay.co`)
- **What it fetches:** Longest-running ad creatives across Facebook/Instagram
- **Key data:** Brand name, running duration (days), full transcription, headline, display format
- **Auth:** Raw API key in `Authorization` header (no Bearer prefix)
- **Config:** `foreplay.api_key`, `foreplay.min_running_days`

### Facebook Ads Library (`fb_ads_library_source.py`)
- **API:** Meta Graph API v19.0 Ads Archive
- **What it fetches:** Active US ads matching the niche topic, filtered to 30+ days running
- **Key data:** Page name, ad body copy, title, days running
- **Auth:** Meta access token
- **Config:** `fb_ads_library.access_token`, `fb_ads_library.min_running_days`

### YouTube (`youtube_source.py`)
- **API:** YouTube Data API v3 + youtube-transcript-api
- **What it fetches:** Top ad/commercial videos by view count, with first-60s transcript extraction
- **Key data:** Video title, channel, hook text (first 60 seconds of transcript or first 200 chars of description)
- **Auth:** YouTube Data API key
- **Config:** `youtube.api_key`, `youtube.include_transcripts`

### Anstrex (`anstrex_source.py`)
- **Method:** Session-based scraping (no public API)
- **What it fetches:** Native ad creatives sorted by running duration
- **Key data:** Title, description, advertiser, days running, landing URL
- **Auth:** Username/password login or pre-authenticated session cookies
- **Config:** `anstrex.username`, `anstrex.password`, `anstrex.session_cookies`
- **Note:** Anstrex uses Cloudflare protection. This source is best-effort and will gracefully return an empty list on any error.

## Usage

Sources are called by `run_intel_harvest()` in `harvest.py` when `intel_harvest.enabled` is set to `true` in config. Each source is wrapped in try/except — if one fails, the others continue.

All results are saved to `loop/learnings/intel_YYYYMMDD_HHMMSS.json` and analyzed by `analyze_intel()` in `analyze.py`.

## Adding a New Source

1. Create `loop/scripts/sources/your_source.py`
2. Implement `fetch_your_source(config, topic, limit, dry_run) -> list[dict]`
3. Add the call to `run_intel_harvest()` in `harvest.py`
4. Add config keys to `config.yaml.example`

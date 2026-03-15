"""
foreplay_source.py — Fetch longest-running ad creatives from Foreplay's discovery API.

Returns enriched ad dicts with brand, running duration, transcription, and format info.
"""

import time
import requests


def fetch_foreplay_ads(
    config: dict, topic: str, limit: int = 50, dry_run: bool = False
) -> list[dict]:
    """
    Pull top ads from Foreplay ordered by longest running duration.

    Config keys:
      - foreplay.api_key: Raw API key (no Bearer prefix)
      - foreplay.min_running_days: Minimum days running to include (default 30)
    """
    if dry_run:
        print("[FOREPLAY] DRY RUN — returning mock data")
        return [
            {
                "brand_name": "MockBrand Alpha",
                "running_days": 120,
                "full_transcription": "Are you tired of overpaying? Discover the secret...",
                "headline": "Stop Overpaying Today",
                "description": "Join thousands who switched and saved.",
                "display_format": "video",
                "publisher_platform": "facebook",
                "link_url": "https://example.com/mock1",
            },
            {
                "brand_name": "MockBrand Beta",
                "running_days": 95,
                "full_transcription": "What if I told you there's a better way...",
                "headline": "The Better Way",
                "description": "See why experts recommend this approach.",
                "display_format": "image",
                "publisher_platform": "instagram",
                "link_url": "https://example.com/mock2",
            },
        ]

    fp_cfg = config.get("foreplay", {})
    api_key = fp_cfg.get("api_key", "")
    min_days = fp_cfg.get("min_running_days", 30)

    if not api_key:
        print("[FOREPLAY] No API key configured — skipping")
        return []

    base_url = "https://public.api.foreplay.co"
    headers = {"Authorization": api_key}
    params = {
        "q": topic,
        "order": "longest_running",
        "running_duration_min_days": min_days,
        "limit": limit,
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.get(
                f"{base_url}/api/discovery/ads",
                headers=headers,
                params=params,
                timeout=30,
            )

            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"[FOREPLAY] Rate limited (429) — retrying in {wait}s")
                time.sleep(wait)
                continue

            if resp.status_code == 402:
                print("[FOREPLAY] Payment required (402) — check plan limits")
                return []

            resp.raise_for_status()
            raw_ads = resp.json()
            break

        except requests.exceptions.HTTPError:
            raise
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"[FOREPLAY] Request error: {e} — retrying in {wait}s")
                time.sleep(wait)
            else:
                print(f"[FOREPLAY] Failed after {max_retries} attempts: {e}")
                return []
    else:
        print("[FOREPLAY] Exhausted retries on 429 rate limits")
        return []

    if isinstance(raw_ads, dict):
        raw_ads = raw_ads.get("data", raw_ads.get("ads", [raw_ads]))

    results = []
    for ad in raw_ads:
        duration = ad.get("running_duration", {})
        running_days = duration.get("days", 0) if isinstance(duration, dict) else 0

        results.append({
            "brand_name": ad.get("brand_name", ""),
            "running_days": running_days,
            "full_transcription": ad.get("full_transcription", ""),
            "headline": ad.get("headline", ""),
            "description": ad.get("description", ""),
            "display_format": ad.get("display_format", ""),
            "publisher_platform": ad.get("publisher_platform", ""),
            "link_url": ad.get("link_url", ""),
        })

    print(f"[FOREPLAY] Fetched {len(results)} ads for '{topic}'")
    return results

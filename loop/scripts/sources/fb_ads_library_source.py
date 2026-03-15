"""
fb_ads_library_source.py — Fetch long-running ads from the Facebook Ads Library API.

Filters for ads running 30+ days and returns ad copy, page, and duration info.
"""

import requests
from datetime import datetime


def fetch_fb_ads(
    config: dict, topic: str, limit: int = 50, dry_run: bool = False
) -> list[dict]:
    """
    Search the Facebook Ads Library for ads matching the topic.

    Config keys:
      - fb_ads_library.access_token: Meta Graph API access token
      - fb_ads_library.min_running_days: Minimum days running (default 30)
    """
    if dry_run:
        print("[FB_ADS] DRY RUN — returning mock data")
        return [
            {
                "page_name": "MockPage Insurance",
                "ad_body": "Don't let your rates go up. Compare quotes in 60 seconds.",
                "title": "Compare & Save on Auto Insurance",
                "days_running": 67,
            },
            {
                "page_name": "MockPage Finance",
                "ad_body": "Homeowners are saving an average of $1,200/year.",
                "title": "Switch & Save Today",
                "days_running": 45,
            },
        ]

    fb_cfg = config.get("fb_ads_library", {})
    access_token = fb_cfg.get("access_token", "")
    min_days = fb_cfg.get("min_running_days", 30)

    if not access_token:
        print("[FB_ADS] No access token configured — skipping")
        return []

    url = "https://graph.facebook.com/v19.0/ads_archive"
    params = {
        "search_terms": topic,
        "ad_reached_countries": '["US"]',
        "fields": ",".join([
            "id",
            "ad_creative_bodies",
            "ad_creative_link_titles",
            "page_name",
            "ad_delivery_start_time",
        ]),
        "limit": limit,
        "access_token": access_token,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except requests.exceptions.RequestException as e:
        print(f"[FB_ADS] Request error: {e}")
        return []

    now = datetime.now()
    results = []
    for ad in data:
        start_str = ad.get("ad_delivery_start_time", "")
        if not start_str:
            continue

        try:
            start_date = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            days_running = (now - start_date.replace(tzinfo=None)).days
        except (ValueError, TypeError):
            continue

        if days_running < min_days:
            continue

        bodies = ad.get("ad_creative_bodies", [])
        titles = ad.get("ad_creative_link_titles", [])

        results.append({
            "page_name": ad.get("page_name", ""),
            "ad_body": bodies[0] if bodies else "",
            "title": titles[0] if titles else "",
            "days_running": days_running,
        })

    print(f"[FB_ADS] Fetched {len(results)} ads running {min_days}+ days for '{topic}'")
    return results

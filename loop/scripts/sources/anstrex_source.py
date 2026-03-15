"""
anstrex_source.py — Fetch native/push/pops ad intelligence from Anstrex.

Uses the internal Anstrex app API (api.anstrex.com) which is authenticated
via a Bearer token obtained from the Anstrex app. This is NOT the official
paid REST API (restapi.anstrex.com) — that requires a separate paid API package.

sort_id=10 = Duration descending (longest-running first) per Marcel's doctrine.
"""

import requests
from datetime import datetime


# sort_id reference (from /api/v1/en/adv/data metadata endpoint)
# 9 = Duration asc, 10 = Duration desc (longest-running = most profitable signal)
# 1/2 = Ad Gravity, 3/4 = Ad Strength, 5/6 = Date First Seen, 7/8 = Date Last Seen
_SORT_DURATION_DESC = 10

_BASE_URL = "https://api.anstrex.com/api/v1/en"
_HEADERS = {
    "Origin": "https://native.anstrex.com",
    "Referer": "https://native.anstrex.com/",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    ),
}


def fetch_anstrex_ads(
    config: dict,
    topic: str,
    platform: str = "native",
    limit: int = 30,
    dry_run: bool = False,
) -> list[dict]:
    """
    Fetch ad intelligence from Anstrex internal API.

    Config keys:
      - anstrex.bearer_token: App bearer token (e.g. '631281|opCWb4...')
      - anstrex.platform: 'native', 'push', or 'pops' (default 'native')
      - anstrex.min_running_days: Only return ads running this many days+ (default 30)

    Returns list of dicts with: title, sub_text, landing_url, days_running,
    ad_networks, geos, ad_strength, gravity, created_at
    """
    if dry_run:
        print("[ANSTREX] DRY RUN — returning mock data")
        return [
            {
                "title": "Drivers Are Switching to This New Policy",
                "sub_text": "insurancequotes.com",
                "landing_url": "https://example.com/lander1",
                "days_running": 245,
                "ad_networks": ["Taboola"],
                "geos": ["US"],
                "ad_strength": 850,
                "gravity": 12,
                "created_at": "2024-07-01 00:00:00",
            },
            {
                "title": "The Insurance Trick They Don't Want You to Know",
                "sub_text": "saveoninsurance.net",
                "landing_url": "https://example.com/lander2",
                "days_running": 180,
                "ad_networks": ["Outbrain"],
                "geos": ["US", "CA"],
                "ad_strength": 620,
                "gravity": 8,
                "created_at": "2024-09-01 00:00:00",
            },
        ]

    anstrex_cfg = config.get("anstrex", {})
    bearer_token = anstrex_cfg.get("bearer_token", "")
    platform = anstrex_cfg.get("platform", platform)
    min_days = anstrex_cfg.get("min_running_days", 30)

    if not bearer_token:
        print("[ANSTREX] No bearer_token configured — skipping")
        return []

    headers = {**_HEADERS, "Authorization": f"Bearer {bearer_token}"}

    try:
        pages_needed = max(1, (limit + 29) // 30)  # 30 ads per page
        results = []
        now = datetime.now()

        for page in range(1, pages_needed + 1):
            r = requests.get(
                f"{_BASE_URL}/creative/search",
                params={
                    "product_name": platform,
                    "product_type": "creative",
                    "keyword": topic,
                    "sort_id": _SORT_DURATION_DESC,
                    "page": page,
                    "additional_data": "eyJpcCI6bnVsbH0=",  # {"ip": null}
                },
                headers=headers,
                timeout=15,
            )

            if r.status_code == 401:
                print("[ANSTREX] Bearer token expired or invalid")
                return results

            if r.status_code != 200:
                print(f"[ANSTREX] Request failed (status {r.status_code})")
                break

            page_data = r.json().get("data", {})
            ads = page_data.get("data", [])

            if not ads:
                break

            for ad in ads:
                src = ad.get("_source", {})
                created_str = src.get("created_at", "")
                try:
                    created_dt = datetime.strptime(created_str, "%Y-%m-%d %H:%M:%S")
                    days_running = (now - created_dt).days
                except (ValueError, TypeError):
                    days_running = 0

                # Apply minimum days filter
                if days_running < min_days:
                    continue

                results.append({
                    "title": src.get("title", ""),
                    "sub_text": src.get("sub_text", ""),
                    "landing_url": src.get("landing_page_url", ""),
                    "days_running": days_running,
                    "ad_networks": src.get("ad_network_names", []),
                    "geos": src.get("geo_names", []),
                    "ad_strength": src.get("ad_strength", 0),
                    "gravity": src.get("gravity", 0),
                    "created_at": created_str,
                })

                if len(results) >= limit:
                    break

            if len(results) >= limit:
                break

        print(f"[ANSTREX] Fetched {len(results)} {platform} ads for '{topic}' (30+ days running)")
        return results

    except Exception as e:
        print(f"[ANSTREX] Error (graceful degradation): {e}")
        return []

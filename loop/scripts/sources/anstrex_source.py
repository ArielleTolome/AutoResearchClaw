"""
anstrex_source.py — Scrape native ad data from Anstrex (best-effort, graceful degradation).

Anstrex uses Cloudflare protection, so this source may fail frequently.
All errors are caught and return an empty list rather than crashing the pipeline.
"""

import requests


def fetch_anstrex_ads(
    config: dict,
    topic: str,
    platform: str = "native",
    limit: int = 30,
    dry_run: bool = False,
) -> list[dict]:
    """
    Attempt to fetch native ad data from Anstrex.

    Config keys:
      - anstrex.username: Account email
      - anstrex.password: Account password
      - anstrex.session_cookies: Pre-authenticated cookie string (optional)
      - anstrex.platform: Ad platform to search (default "native")

    Note: Anstrex uses Cloudflare protection. This source is best-effort —
    if any step fails, it returns an empty list and logs a warning.
    """
    if dry_run:
        print("[ANSTREX] DRY RUN — returning mock data")
        return [
            {
                "title": "Drivers Are Switching to This New Policy",
                "description": "Find out why thousands are making the change.",
                "advertiser": "MockAdvertiser",
                "days_running": 42,
                "landing_url": "https://example.com/lander1",
            },
            {
                "title": "The Insurance Trick They Don't Want You to Know",
                "description": "Save up to 50% with this one simple step.",
                "advertiser": "MockNativeAds",
                "days_running": 38,
                "landing_url": "https://example.com/lander2",
            },
        ]

    anstrex_cfg = config.get("anstrex", {})
    username = anstrex_cfg.get("username", "")
    password = anstrex_cfg.get("password", "")
    session_cookies = anstrex_cfg.get("session_cookies", "")
    platform = anstrex_cfg.get("platform", platform)

    if not session_cookies and not (username and password):
        print("[ANSTREX] No credentials configured — skipping")
        return []

    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        })

        if session_cookies:
            # Use pre-authenticated cookies directly
            for cookie_pair in session_cookies.split(";"):
                cookie_pair = cookie_pair.strip()
                if "=" in cookie_pair:
                    name, value = cookie_pair.split("=", 1)
                    session.cookies.set(name.strip(), value.strip())
        else:
            # Attempt login
            login_resp = session.post(
                "https://app.anstrex.com/login",
                data={"email": username, "password": password},
                timeout=30,
            )
            if login_resp.status_code != 200:
                print(f"[ANSTREX] Login failed (status {login_resp.status_code})")
                return []

        # Search for ads
        search_resp = session.get(
            "https://app.anstrex.com/search",
            params={
                "q": topic,
                "platform": platform,
                "sort": "running_days",
                "order": "desc",
                "limit": limit,
            },
            timeout=30,
        )

        if search_resp.status_code != 200:
            print(f"[ANSTREX] Search failed (status {search_resp.status_code}) — Cloudflare may be blocking")
            return []

        # Attempt to parse JSON response
        data = search_resp.json()
        if isinstance(data, dict):
            data = data.get("data", data.get("ads", []))

        results = []
        for ad in data[:limit]:
            results.append({
                "title": ad.get("title", ""),
                "description": ad.get("description", ""),
                "advertiser": ad.get("advertiser", ad.get("advertiser_name", "")),
                "days_running": ad.get("days_running", ad.get("running_days", 0)),
                "landing_url": ad.get("landing_url", ad.get("url", "")),
            })

        print(f"[ANSTREX] Fetched {len(results)} {platform} ads for '{topic}'")
        return results

    except Exception as e:
        print(f"[ANSTREX] Error (graceful degradation): {e}")
        return []

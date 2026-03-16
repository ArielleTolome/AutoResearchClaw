#!/usr/bin/env python3
"""
meta_ad_library.py — Meta Ad Library scraper for competitive intelligence.
Searches the Meta Ad Library API for active ads by vertical keyword,
extracts hooks, scores freshness, and posts intel to Discord.

Usage:
  python meta_ad_library.py                          # All verticals
  python meta_ad_library.py --vertical Medicare       # Single vertical
  python meta_ad_library.py --dry-run                # Don't post to Discord
  python meta_ad_library.py --limit 20               # Cap results per vertical
"""

import os, sys, json, argparse, datetime
from pathlib import Path

import requests
import yaml

# ── Config ───────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"

def _load_config() -> dict:
    if CFG_PATH.exists():
        return yaml.safe_load(CFG_PATH.read_text()) or {}
    return {}

CFG = _load_config()
META_ACCESS_TOKEN = CFG.get("meta", {}).get("access_token", os.getenv("META_ACCESS_TOKEN", ""))
COMPETITOR_WEBHOOK = (
    CFG.get("discord", {}).get("competitor_webhook_url")
    or os.getenv("DISCORD_COMPETITOR_WEBHOOK", "")
)

API_BASE = "https://graph.facebook.com/v21.0/ads_archive"
API_FIELDS = (
    "id,ad_creative_bodies,ad_creative_link_captions,ad_creative_link_titles,"
    "spend,impressions,ad_delivery_start_time,page_name,ad_snapshot_url"
)

VERTICAL_KEYWORDS = {
    "Medicare":        "Medicare Advantage supplement",
    "Auto Insurance":  "car insurance quote",
    "ACA":             "health insurance marketplace",
    "Debt Relief":     "debt consolidation relief",
    "Final Expense":   "final expense life insurance",
}

# ── API helpers ──────────────────────────────────────────────────────────────

def _fetch_ads(search_terms: str, limit: int = 25, after: str = None) -> tuple[list[dict], str | None]:
    """
    Fetch ads from Meta Ad Library API.
    Returns (ads_list, next_cursor_or_None).
    """
    if not META_ACCESS_TOKEN:
        print("[meta] ERROR: No meta.access_token configured")
        print("  Set meta.access_token in config.yaml or META_ACCESS_TOKEN env var")
        return [], None

    params = {
        "access_token":         META_ACCESS_TOKEN,
        "ad_type":              "ALL",
        "ad_reached_countries": '["US"]',
        "search_terms":         search_terms,
        "fields":               API_FIELDS,
        "limit":                min(limit, 50),
    }
    if after:
        params["after"] = after

    try:
        r = requests.get(API_BASE, params=params, timeout=30)
        if r.status_code == 400:
            err = r.json().get("error", {})
            print(f"[meta] API error: {err.get('message', r.text[:200])}")
            if "OAuthException" in str(err.get("type", "")):
                print("  → Your Meta access token may be expired. Generate a new one at:")
                print("    https://developers.facebook.com/tools/explorer/")
            return [], None
        r.raise_for_status()
        data = r.json()
        ads = data.get("data", [])
        paging = data.get("paging", {})
        next_cursor = paging.get("cursors", {}).get("after") if paging.get("next") else None
        return ads, next_cursor
    except requests.exceptions.HTTPError as e:
        print(f"[meta] HTTP error: {e}")
        return [], None
    except Exception as e:
        print(f"[meta] Fetch error: {e}")
        return [], None


def _extract_hook(ad: dict) -> str:
    """Extract the hook (first line) from ad creative body."""
    bodies = ad.get("ad_creative_bodies", [])
    if not bodies:
        return ""
    text = bodies[0]
    # First line or first sentence
    first_line = text.split("\n")[0].strip()
    if len(first_line) > 150:
        first_line = first_line[:150] + "..."
    return first_line


def _score_freshness(ad: dict) -> str:
    """Score ad freshness based on delivery start date."""
    start_str = ad.get("ad_delivery_start_time", "")
    if not start_str:
        return "Unknown"
    try:
        start = datetime.datetime.strptime(start_str[:10], "%Y-%m-%d").date()
        days_running = (datetime.date.today() - start).days
        if days_running > 30:
            return "Veteran"
        elif days_running >= 7:
            return "Active"
        else:
            return "New"
    except Exception:
        return "Unknown"


def _parse_ad(ad: dict, vertical: str) -> dict:
    """Parse a raw API ad into a structured dict."""
    hook = _extract_hook(ad)
    freshness = _score_freshness(ad)
    page_name = ad.get("page_name", "Unknown")
    titles = ad.get("ad_creative_link_titles", [])
    captions = ad.get("ad_creative_link_captions", [])

    # Spend bucket (if available — often restricted)
    spend = ad.get("spend", {})
    spend_str = ""
    if isinstance(spend, dict):
        lo = spend.get("lower_bound", "")
        hi = spend.get("upper_bound", "")
        if lo or hi:
            spend_str = f"${lo}-${hi}"

    return {
        "id":          ad.get("id", ""),
        "vertical":    vertical,
        "page_name":   page_name,
        "hook":        hook,
        "title":       titles[0] if titles else "",
        "caption":     captions[0] if captions else "",
        "freshness":   freshness,
        "spend":       spend_str,
        "start_date":  ad.get("ad_delivery_start_time", "")[:10],
        "snapshot_url": ad.get("ad_snapshot_url", ""),
    }


# ── Discord posting ──────────────────────────────────────────────────────────

FRESHNESS_COLOR = {
    "Veteran": 0xE74C3C,   # red — long-running = likely winning
    "Active":  0xE67E22,   # orange
    "New":     0x3498DB,   # blue
    "Unknown": 0x95A5A6,
}

def _post_to_discord(ads: list[dict]):
    """Post high-value ads (Veteran + Active) to competitor-watch channel."""
    if not COMPETITOR_WEBHOOK:
        print("[meta] No competitor_webhook_url configured — skipping Discord")
        return

    valuable = [a for a in ads if a["freshness"] in ("Veteran", "Active")]
    if not valuable:
        print("[meta] No veteran/active ads to post")
        return

    for ad in valuable[:10]:  # Cap at 10 embeds per run
        fields = [
            {"name": "🎣 Hook", "value": ad["hook"][:500] or "N/A", "inline": False},
            {"name": "📄 Page", "value": ad["page_name"][:100], "inline": True},
            {"name": "⏱️ Freshness", "value": ad["freshness"], "inline": True},
            {"name": "📅 Started", "value": ad["start_date"] or "Unknown", "inline": True},
        ]
        if ad["spend"]:
            fields.append({"name": "💰 Spend", "value": ad["spend"], "inline": True})
        if ad["snapshot_url"]:
            fields.append({"name": "🔗 Snapshot", "value": f"[View Ad]({ad['snapshot_url']})", "inline": True})

        embed = {
            "title": f"📊 Competitor Ad — {ad['vertical']}",
            "description": f"**{ad['title'][:200]}**" if ad["title"] else None,
            "color": FRESHNESS_COLOR.get(ad["freshness"], 0x95A5A6),
            "fields": fields,
            "footer": {"text": f"Meta Ad Library · {ad['freshness']} · AutoResearchClaw v2.0"},
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        # Remove None description
        embed = {k: v for k, v in embed.items() if v is not None}

        try:
            r = requests.post(COMPETITOR_WEBHOOK, json={"embeds": [embed]}, timeout=10)
            if r.status_code in (200, 204):
                print(f"  [discord] Posted: {ad['page_name'][:30]} — {ad['hook'][:40]}")
            else:
                print(f"  [discord] Error {r.status_code}: {r.text[:100]}")
        except Exception as e:
            print(f"  [discord] Post error: {e}")


# ── Main ─────────────────────────────────────────────────────────────────────

def scrape_vertical(vertical: str, keyword: str, limit: int = 25) -> list[dict]:
    """Scrape ads for a single vertical with pagination."""
    all_ads = []
    cursor = None
    remaining = limit

    while remaining > 0:
        batch_size = min(remaining, 50)
        ads, next_cursor = _fetch_ads(keyword, limit=batch_size, after=cursor)
        if not ads:
            break
        for ad in ads:
            all_ads.append(_parse_ad(ad, vertical))
        remaining -= len(ads)
        cursor = next_cursor
        if not cursor:
            break

    return all_ads


def main():
    parser = argparse.ArgumentParser(description="Meta Ad Library scraper for AutoResearchClaw")
    parser.add_argument("--vertical", help="Single vertical to scrape (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Don't post to Discord")
    parser.add_argument("--limit", type=int, default=25, help="Max ads per vertical (default: 25)")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()

    if not META_ACCESS_TOKEN:
        print("[meta] ERROR: No Meta access token configured")
        print("  Set meta.access_token in loop/config/config.yaml")
        print("  Or export META_ACCESS_TOKEN=<your_token>")
        sys.exit(1)

    verticals = VERTICAL_KEYWORDS
    if args.vertical:
        if args.vertical in verticals:
            verticals = {args.vertical: verticals[args.vertical]}
        else:
            print(f"[meta] Unknown vertical: {args.vertical}")
            print(f"  Available: {', '.join(VERTICAL_KEYWORDS.keys())}")
            sys.exit(1)

    all_ads = []
    for vertical, keyword in verticals.items():
        print(f"\n[meta] Scraping: {vertical} ('{keyword}')")
        ads = scrape_vertical(vertical, keyword, limit=args.limit)
        print(f"  Found {len(ads)} ads")

        # Stats
        by_freshness = {}
        for a in ads:
            by_freshness[a["freshness"]] = by_freshness.get(a["freshness"], 0) + 1
        print(f"  Freshness breakdown: {json.dumps(by_freshness)}")

        all_ads.extend(ads)

    if args.json:
        print(json.dumps(all_ads, indent=2))
    else:
        # Summary
        print(f"\n{'='*60}")
        print(f"  META AD LIBRARY REPORT")
        print(f"  {len(all_ads)} total ads across {len(verticals)} verticals")
        print(f"{'='*60}")
        for ad in all_ads[:20]:
            print(f"\n  [{ad['freshness']}] {ad['vertical']} — {ad['page_name'][:30]}")
            print(f"    Hook: {ad['hook'][:80]}")
            if ad["spend"]:
                print(f"    Spend: {ad['spend']}")

    if not args.dry_run:
        _post_to_discord(all_ads)

    print(f"\n✅ Done — {len(all_ads)} ads scraped")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
competitor_watcher.py — Competitor ad intelligence for AutoResearchClaw
Searches Facebook Ads Library for active leadgen ads, scores with Codex CLI,
fires Discord embeds to #competitor-watch for Medium/High threat ads.

Usage:
  python competitor_watcher.py [--dry-run] [--vertical NAME]
"""

import os, sys, json, re, time, hashlib, argparse, datetime, subprocess
from pathlib import Path

import requests
import yaml

# ── Config ───────────────────────────────────────────────────────────────────
CFG_PATH  = Path(__file__).parent.parent / "config" / "config.yaml"
STATE_DIR = Path(__file__).parent.parent / "state"
SEEN_PATH = STATE_DIR / "competitor_seen.json"

def load_config() -> dict:
    if CFG_PATH.exists():
        return yaml.safe_load(CFG_PATH.read_text()) or {}
    return {}

CFG                 = load_config()
COMPETITOR_WEBHOOK  = CFG.get("discord", {}).get("competitor_webhook_url", os.getenv("COMPETITOR_WEBHOOK_URL", ""))
FB_ACCESS_TOKEN     = CFG.get("facebook", {}).get("access_token", os.getenv("FB_ACCESS_TOKEN", ""))

# Vertical → search query
VERTICAL_QUERIES = [
    ("Auto Insurance",       "car insurance quote"),
    ("Medicare",             "Medicare Advantage 2026"),
    ("ACA / Health",         "health insurance quote"),
    ("Debt Settlement",      "debt relief program"),
    ("Tax Settlement",       "tax relief IRS"),
    ("Final Expense",        "final expense insurance"),
    ("Home Insurance",       "home insurance quote"),
]

SCORE_PROMPT = """Analyze this Facebook ad for leadgen competitive intelligence. Respond with ONLY a JSON object, nothing else.

JSON schema:
{{
  "vertical": "one of: Auto Insurance | Home Insurance | Medicare | Final Expense | ACA | U65 Private Health | Debt Settlement | Tax Settlement | Home Services | Personal Injury | Mass Tort | Other",
  "hook_type": "one of: Question | Statistic | Testimonial | Offer | Fear/Pain | Curiosity | Direct CTA | Other",
  "awareness_stage": 1, 2, 3, 4, or 5,
  "longevity_signal": "one of: New (<7d) | Established (7-30d) | Veteran (30d+)",
  "threat_level": "one of: Low | Medium | High",
  "hook_summary": "1 sentence — what makes this hook work or fail"
}}

threat_level guide:
- High = ad has been running 30+ days AND has a strong hook (testimonial, specific stat, or compelling offer)
- Medium = established ad (7-30d) OR new ad with strong hook
- Low = everything else

Ad details:
Advertiser: {advertiser}
Running since: {start_date}
Ad copy: {body}"""


# ── State ─────────────────────────────────────────────────────────────────────
def load_seen() -> set:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if SEEN_PATH.exists():
        return set(json.loads(SEEN_PATH.read_text()))
    return set()

def save_seen(seen: set):
    SEEN_PATH.write_text(json.dumps(list(seen)))

def ad_hash(advertiser: str, body: str) -> str:
    key = (advertiser + body[:50]).lower()
    return hashlib.md5(key.encode()).hexdigest()[:12]


# ── FB Ads Library fetch ───────────────────────────────────────────────────────
def fetch_ads(query: str) -> list[dict]:
    """
    Try FB Ads Library API. Falls back gracefully if unavailable.
    Uses the access token if configured, otherwise tries the public endpoint.
    """
    ads = []

    # Primary: official API with token
    if FB_ACCESS_TOKEN:
        url = "https://graph.facebook.com/v19.0/ads_archive"
        params = {
            "access_token":   FB_ACCESS_TOKEN,
            "search_terms":   query,
            "ad_type":        "ALL",
            "ad_active_status": "ACTIVE",
            "limit":          10,
            "fields":         "page_name,ad_creative_bodies,ad_delivery_start_time,ad_snapshot_url",
            "search_countries": ["US"],
        }
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json().get("data", [])
            for ad in data:
                bodies = ad.get("ad_creative_bodies") or []
                body = bodies[0] if bodies else ""
                ads.append({
                    "advertiser":  ad.get("page_name", "Unknown"),
                    "body":        body[:300],
                    "start_date":  ad.get("ad_delivery_start_time", ""),
                    "snapshot_url": ad.get("ad_snapshot_url", ""),
                })
            return ads
        except Exception as e:
            print(f"  [WARN] FB API failed: {e} — trying public endpoint")

    # Fallback: public async search (limited, may not return data)
    try:
        url = "https://www.facebook.com/ads/library/async/search_ads/"
        params = {
            "q":             query,
            "ad_type":       "all",
            "count":         10,
            "active_status": "active",
            "country":       "US",
            "media_type":    "all",
        }
        headers = {"User-Agent": "AutoResearchClaw/1.9 competitor-research"}
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        # This endpoint may return JS-rendered data or a JSON payload
        try:
            data = r.json()
            for key in ("data", "results", "ads", "payload"):
                if isinstance(data.get(key), list):
                    for ad in data[key]:
                        ads.append({
                            "advertiser":  ad.get("page_name", ad.get("advertiser_name", "Unknown")),
                            "body":        (ad.get("ad_creative_body") or ad.get("body") or "")[:300],
                            "start_date":  ad.get("ad_delivery_start_time", ad.get("start_date", "")),
                            "snapshot_url": ad.get("ad_snapshot_url", ""),
                        })
                    break
        except Exception:
            pass
    except Exception as e:
        print(f"  [WARN] Public endpoint failed: {e}")

    return ads


# ── Scoring via Codex CLI ─────────────────────────────────────────────────────
def score_ad(ad: dict) -> dict | None:
    # Parse start date for longevity
    start_str = ad.get("start_date", "")
    try:
        start_dt = datetime.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        days_running = (datetime.datetime.now(datetime.timezone.utc) - start_dt).days
    except Exception:
        days_running = 0

    if days_running >= 30:
        longevity = "Veteran (30d+)"
    elif days_running >= 7:
        longevity = "Established (7-30d)"
    else:
        longevity = "New (<7d)"

    prompt = SCORE_PROMPT.format(
        advertiser=ad.get("advertiser", "Unknown"),
        start_date=f"{start_str} ({days_running} days ago)" if start_str else "Unknown",
        body=ad.get("body", "(no copy available)"),
    )

    try:
        result = subprocess.run(
            ["codex", "exec", "-c", "model=gpt-5.3-codex", "-c", "approval_policy=never", "-"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=45,
        )
        output = result.stdout.strip()
        json_matches = re.findall(r'\{[^{}]+\}', output, re.DOTALL)
        if not json_matches:
            return None
        scored = json.loads(json_matches[-1])
        scored["days_running"] = days_running
        scored["longevity_signal"] = longevity  # override with calculated value
        return scored
    except subprocess.TimeoutExpired:
        print(f"  [WARN] Codex timeout on: {ad['advertiser']}")
        return None
    except Exception as e:
        print(f"  [WARN] Scoring failed: {e}")
        return None


# ── Discord embed ─────────────────────────────────────────────────────────────
def fire_discord(ad: dict, scored: dict, dry_run: bool):
    if not COMPETITOR_WEBHOOK:
        print("  [WARN] No competitor_webhook_url configured — skipping")
        return

    threat = scored.get("threat_level", "Low")
    color = 0xE74C3C if threat == "High" else 0xE67E22  # red or orange

    embed = {
        "title":       ad.get("advertiser", "Unknown Advertiser")[:256],
        "description": (ad.get("body") or "(no copy)")[:500],
        "color":       color,
        "fields": [
            {"name": "Hook Type",      "value": scored.get("hook_type", "?"),         "inline": True},
            {"name": "Awareness Stage","value": str(scored.get("awareness_stage", "?")), "inline": True},
            {"name": "Longevity",      "value": scored.get("longevity_signal", "?"),  "inline": True},
            {"name": "Threat",         "value": f"{'🔴' if threat == 'High' else '🟠'} {threat}", "inline": True},
            {"name": "Analysis",       "value": scored.get("hook_summary", "")[:200], "inline": False},
        ],
        "footer":    {"text": scored.get("vertical", "")},
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }
    if ad.get("snapshot_url"):
        embed["url"] = ad["snapshot_url"]

    if dry_run:
        print(f"    [DRY-RUN] Discord: {ad['advertiser']} | {threat} threat | {scored.get('hook_type')}")
        return
    try:
        requests.post(COMPETITOR_WEBHOOK, json={"embeds": [embed]}, timeout=10)
    except Exception as e:
        print(f"  [WARN] Discord post failed: {e}")


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--vertical", help="Run only this vertical")
    args = parser.parse_args()

    seen = load_seen()
    queries = VERTICAL_QUERIES
    if args.vertical:
        queries = [(v, q) for v, q in VERTICAL_QUERIES if args.vertical.lower() in v.lower()]
        if not queries:
            print(f"[ERROR] No vertical matching '{args.vertical}'")
            sys.exit(1)

    total_new = 0

    for vertical, query in queries:
        print(f"\n[{vertical}] query: \"{query}\"")
        ads = fetch_ads(query)
        print(f"  {len(ads)} ads fetched")

        for ad in ads:
            h = ad_hash(ad["advertiser"], ad["body"])
            if h in seen:
                continue
            seen.add(h)

            scored = score_ad(ad)
            if not scored:
                print(f"  [skip] {ad['advertiser']}")
                continue

            threat = scored.get("threat_level", "Low")
            print(f"  [{threat}] {ad['advertiser']} | {scored.get('hook_type')} | {scored.get('longevity_signal')}")

            if threat in ("Medium", "High"):
                fire_discord(ad, scored, args.dry_run)
                total_new += 1

            time.sleep(0.3)

    save_seen(seen)
    print(f"\n✅ Done — {total_new} Medium/High threat ads signaled to #competitor-watch")


if __name__ == "__main__":
    main()

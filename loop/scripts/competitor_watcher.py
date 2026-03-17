#!/usr/bin/env python3
from __future__ import annotations
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
ANSTREX_TOKEN       = CFG.get("anstrex", {}).get("bearer_token", os.getenv("ANSTREX_TOKEN", "631281|opCWb4Y22xWM1AidmVUjyLVAFe0y08F0uesjEQqy71c6a5b3"))

# Vertical → search keyword for Anstrex native ads
VERTICAL_QUERIES = [
    ("Auto Insurance",   "car insurance"),
    ("Medicare",         "Medicare Advantage"),
    ("ACA / Health",     "health insurance"),
    ("Debt Settlement",  "debt relief"),
    ("Tax Settlement",   "tax relief"),
    ("Final Expense",    "final expense"),
    ("Home Insurance",   "home insurance"),
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


# ── Anstrex native ad fetch ───────────────────────────────────────────────────
def fetch_ads(keyword: str) -> list[dict]:
    """
    Fetch native ads from Anstrex internal API.
    sort_id=10 = Duration desc (longest-running first).
    """
    ads = []
    try:
        r = requests.get(
            "https://api.anstrex.com/api/v1/en/creative/search",
            params={
                "product_name":    "native",
                "product_type":    "creative",
                "keyword":         keyword,
                "sort_id":         10,  # Duration desc
                "page":            1,
                "additional_data": "eyJpcCI6bnVsbH0=",
            },
            headers={
                "Authorization": f"Bearer {ANSTREX_TOKEN}",
                "Origin":        "https://native.anstrex.com",
                "Referer":       "https://native.anstrex.com/",
                "User-Agent":    "AutoResearchClaw/1.9",
            },
            timeout=20,
        )
        r.raise_for_status()
        rdata = r.json()
        # Response is under data.data (not hits.hits)
        hits = rdata.get("data", {}).get("data", []) or rdata.get("hits", {}).get("hits", [])
        for hit in hits[:10]:
            src = hit.get("_source", {})
            title = src.get("title", "")
            sub_text = src.get("sub_text", "")
            body = f"{title} — {sub_text}".strip(" —")
            start_date = src.get("created_at", "")
            networks = ", ".join(src.get("ad_network_names") or [])
            geos = ", ".join(src.get("geo_names") or [])
            lp_url = src.get("landing_page_url", "")
            ads.append({
                "advertiser":  lp_url[:50] or "Unknown",
                "body":        body[:300],
                "start_date":  start_date,
                "snapshot_url": lp_url,
                "networks":    networks,
                "geos":        geos,
            })
    except Exception as e:
        print(f"  [WARN] Anstrex fetch failed for '{keyword}': {e}")
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
            {"name": "Hook Type",      "value": scored.get("hook_type", "?"),              "inline": True},
            {"name": "Awareness Stage","value": str(scored.get("awareness_stage", "?")),   "inline": True},
            {"name": "Longevity",      "value": scored.get("longevity_signal", "?"),       "inline": True},
            {"name": "Threat",         "value": f"{'🔴' if threat == 'High' else '🟠'} {threat}", "inline": True},
            {"name": "Networks",       "value": ad.get("networks", "Unknown") or "Unknown","inline": True},
            {"name": "Analysis",       "value": scored.get("hook_summary", "")[:200],      "inline": False},
        ],
        "footer":    {"text": scored.get("vertical", "")},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
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

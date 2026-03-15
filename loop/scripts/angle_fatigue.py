"""
angle_fatigue.py — Score how saturated each angle is in a given niche.

Doctrine (Marcel + Nick Theriot):
  Angle saturation is the #1 cause of declining hook rates in mature campaigns.
  A fresh angle on a dead vertical can outperform a polished creative on a dead angle.
  
  Signal sources (all from Baserow intel_ads + live competitor data):
  - Volume: how many ads are running this angle right now?
  - Longevity: are new ads on this angle still getting traction?
  - Recency: when did new entrants last appear using this angle?
  - Diversity: is it one big player or a wide field? (wide field = more saturated)
  - Decay: are days_running values trending down for this angle?

Fatigue tiers:
  FRESH       → score < 25      No competition. First-mover window open.
  WARMING     → score 25–50     Growing. Get in now before it saturates.
  SATURATED   → score 51–75     Crowded. Still works but differentiation required.
  DEAD        → score > 75      Overdone. Diminishing returns. Time to rotate.

Output is used by:
  1. generate.py — angle selection gate (block DEAD, flag SATURATED)
  2. Discord embed — fatigue heatmap section in cycle report
  3. prediction_scorer.py — one of 5 scoring inputs
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Angle taxonomy (same 10 angles from ACA + common extras)
# ---------------------------------------------------------------------------
KNOWN_ANGLES = [
    "loyalty_betrayal",       # "I've been a loyal customer for 12 years and they..."
    "pricing_pain",           # "My rate went up 40% with no claims"
    "savings_discovery",      # "Switched in 10 minutes, saved $800/year"
    "abandonment_fear",       # "They cancelled me with no warning"
    "claim_experience",       # "Tried to file a claim and they..."
    "ease_of_use",            # "2 minutes to quote, everything online"
    "social_proof",           # "1.2 million customers trust us"
    "authority",              # "As seen on..." / expert endorsement
    "urgency_scarcity",       # "Rates going up March 1st"
    "transformation",         # Before/after. Life without the pain vs with solution.
    "curiosity_gap",          # "Most people in [city] don't know this about insurance"
    "geo_personalization",    # "Notice for [City] drivers"
    "mechanism",              # New/unique mechanism (Schwartz stage 2-3)
    "testimonial",            # Customer story / UGC
    "negative_hook",          # "Stop doing X" / anti-pattern
    "question",               # Rhetorical question hook
    "news_tie_in",            # Newsjacking / trending event
    "comparison",             # Us vs them
    "loss_aversion",          # What you're losing by not acting
]

# ---------------------------------------------------------------------------
# Saturation scoring weights
# ---------------------------------------------------------------------------
WEIGHT_VOLUME = 0.30        # Raw ad count (more = more saturated)
WEIGHT_DIVERSITY = 0.20     # Advertiser count (many different players = more saturated)
WEIGHT_RECENCY = 0.25       # How recently new entrants appeared (stale = dying or dead)
WEIGHT_LONGEVITY_DECAY = 0.25  # Avg days_running trending down = angle is decaying

VOLUME_CAP = 50             # Normalize at 50 ads per angle
DIVERSITY_CAP = 15          # Normalize at 15 different advertisers per angle
RECENCY_DECAY_DAYS = 30     # No new entrants in 30+ days → recency score maxes out
LONGEVITY_FLOOR_DAYS = 14   # Ads < 14 days are too new to signal anything


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def score_angle_fatigue(
    intel_ads: list[dict],
    niche: str = "",
    dry_run: bool = False,
) -> dict[str, dict]:
    """
    Score angle fatigue from a list of intel_ads dicts.

    Each intel_ad should have: angle, days_running, source, advertiser/page_name, created_at (optional).

    Returns: {angle: {score, tier, volume, diversity, ...}}
    """
    if dry_run:
        print("[FATIGUE] DRY RUN — returning mock fatigue data")
        return _mock_fatigue()

    if not intel_ads:
        print("[FATIGUE] No intel ads to analyze")
        return {}

    now = datetime.utcnow()

    # Group ads by angle
    by_angle: dict[str, list[dict]] = {}
    for ad in intel_ads:
        angle = (ad.get("angle") or "").strip().lower()
        if not angle or angle == "unknown":
            continue
        by_angle.setdefault(angle, []).append(ad)

    # Also include KNOWN_ANGLES with zero data (so we can flag FRESH unexplored angles)
    for a in KNOWN_ANGLES:
        by_angle.setdefault(a, [])

    results = {}
    for angle, ads in by_angle.items():
        if not ads:
            # No competitor data = completely fresh
            results[angle] = {
                "score": 0,
                "tier": "FRESH",
                "volume": 0,
                "diversity": 0,
                "avg_days_running": 0,
                "newest_entrant_days_ago": None,
                "top_advertisers": [],
                "sample_hooks": [],
                "recommendation": "No competitors found on this angle. First-mover opportunity.",
            }
            continue

        # --- Volume score ---
        volume = len(ads)
        volume_score = min(volume / VOLUME_CAP, 1.0) * 100

        # --- Diversity score (unique advertisers) ---
        advertisers = set()
        for ad in ads:
            adv = ad.get("advertiser") or ad.get("page_name") or ad.get("brand_name") or ""
            if adv:
                advertisers.add(adv.lower().strip())
        diversity = len(advertisers)
        diversity_score = min(diversity / DIVERSITY_CAP, 1.0) * 100

        # --- Recency score (how long since new ads appeared on this angle) ---
        # Use created_at if available, otherwise estimate from days_running
        recency_days = None
        for ad in ads:
            created_at = ad.get("created_at")
            if created_at:
                try:
                    ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    age_days = (now - ts.replace(tzinfo=None)).days
                    if recency_days is None or age_days < recency_days:
                        recency_days = age_days
                except Exception:
                    pass

        if recency_days is None:
            # Fallback: newest entrant = ad with smallest days_running
            dr_values = [ad.get("days_running") or 0 for ad in ads]
            min_dr = min(dr_values) if dr_values else 0
            recency_days = min_dr  # proxy: newest ad started running X days ago

        recency_score = min(recency_days / RECENCY_DECAY_DAYS, 1.0) * 100

        # --- Longevity decay score ---
        # If recent ads have low days_running, angle may be dying (competitors pulling out)
        # vs old ads with high days_running = still working
        dr_values = [ad.get("days_running") or 0 for ad in ads if (ad.get("days_running") or 0) >= LONGEVITY_FLOOR_DAYS]
        if dr_values:
            avg_days = sum(dr_values) / len(dr_values)
            # Long-running ads on this angle = it's still working for competitors (not decaying)
            # Score high if recent ads are SHORT (they're dying fast) = angle decaying
            # Score low if avg days is LONG (ads survive long) = angle still productive
            # We invert: short avg = more concerning = higher fatigue contribution
            decay_score = max(0, 100 - min(avg_days / 90, 1.0) * 100)
        else:
            avg_days = 0
            decay_score = 50  # neutral — not enough data

        # --- Composite fatigue score ---
        score = (
            WEIGHT_VOLUME * volume_score
            + WEIGHT_DIVERSITY * diversity_score
            + WEIGHT_RECENCY * recency_score
            + WEIGHT_LONGEVITY_DECAY * decay_score
        )
        score = round(score, 1)

        # --- Tier assignment ---
        tier = _score_to_tier(score)

        # --- Sample hooks (titles from longest-running ads on this angle) ---
        top_ads = sorted(ads, key=lambda x: x.get("days_running") or 0, reverse=True)
        sample_hooks = [ad.get("title", "")[:120] for ad in top_ads[:3] if ad.get("title")]
        top_advertisers = list(advertisers)[:5]

        results[angle] = {
            "score": score,
            "tier": tier,
            "volume": volume,
            "diversity": diversity,
            "avg_days_running": round(avg_days, 0),
            "newest_entrant_days_ago": recency_days,
            "top_advertisers": top_advertisers,
            "sample_hooks": sample_hooks,
            "recommendation": _tier_recommendation(tier, angle),
        }

    # Sort by score descending
    results = dict(sorted(results.items(), key=lambda x: x[1]["score"], reverse=True))

    # Print summary
    print(f"\n[FATIGUE] Angle Fatigue Report — {niche or 'all niches'}")
    print(f"{'Angle':<30} {'Score':>6} {'Tier':<12} {'Volume':>8} {'Advertisers':>12}")
    print("-" * 72)
    for angle, data in results.items():
        if data["volume"] > 0:  # Only print angles with data
            print(f"{angle:<30} {data['score']:>6.1f} {data['tier']:<12} {data['volume']:>8} {data['diversity']:>12}")

    return results


def load_intel_for_niche(niche: str = "") -> list[dict]:
    """
    Load all intel_ads from the latest intel harvest JSON files.
    In production, you'd query Baserow intel_ads table filtered by topic/niche.
    For now, aggregates all local JSON harvest files.
    """
    intel_dir = ROOT / "learnings"
    intel_files = sorted(intel_dir.glob("intel_*.json"), reverse=True)

    all_ads = []
    for f in intel_files[:5]:  # Last 5 harvests
        try:
            data = json.loads(f.read_text())
            for source_ads in data.values():
                if isinstance(source_ads, list):
                    for ad in source_ads:
                        if niche and ad.get("topic", "").lower().replace(" ", "_") != niche.lower().replace(" ", "_"):
                            continue
                        all_ads.append(ad)
        except Exception as e:
            print(f"[FATIGUE] Error reading {f.name}: {e}")

    print(f"[FATIGUE] Loaded {len(all_ads)} intel ads for analysis")
    return all_ads


def load_intel_from_baserow(config: dict, niche: str = "") -> list[dict]:
    """
    Load intel_ads from Baserow for fatigue analysis.
    Falls back to local JSON files if Baserow unavailable.
    """
    try:
        from baserow_sink import BaserowClient
        br_cfg = config.get("baserow", {})
        if not br_cfg.get("enabled"):
            return load_intel_for_niche(niche)

        client = BaserowClient(
            base_url=br_cfg["base_url"],
            email=br_cfg["email"],
            password=br_cfg["password"],
        )
        client.authenticate()

        table_id = br_cfg.get("table_intel_ads_id", 813)
        params = {"size": 200, "order_by": "-days_running"}
        if niche:
            params["filter__topic__contains"] = niche.replace("_", " ")

        r = client.session.get(
            f"{client.base_url}/api/database/rows/table/{table_id}/",
            headers=client._auth_headers(),
            params=params,
            timeout=10,
        )
        if r.status_code == 200:
            rows = r.json().get("results", [])
            print(f"[FATIGUE] Loaded {len(rows)} intel ads from Baserow")
            return rows
    except Exception as e:
        print(f"[FATIGUE] Baserow load failed ({e}), falling back to local files")

    return load_intel_for_niche(niche)


def _score_to_tier(score: float) -> str:
    if score < 25:
        return "FRESH"
    elif score < 50:
        return "WARMING"
    elif score < 75:
        return "SATURATED"
    else:
        return "DEAD"


def _tier_recommendation(tier: str, angle: str) -> str:
    recs = {
        "FRESH": f"🟢 Zero competition on '{angle}'. First-mover advantage — test immediately.",
        "WARMING": f"🟡 '{angle}' is gaining traction. Strong window to enter before saturation.",
        "SATURATED": f"🟠 '{angle}' is crowded. Can still work with strong differentiation — unique mechanism or persona required.",
        "DEAD": f"🔴 '{angle}' is overdone. Rotate out. Try cross-vertical inspiration for fresh angles.",
    }
    return recs.get(tier, "")


def _mock_fatigue() -> dict[str, dict]:
    return {
        "pricing_pain": {"score": 72.4, "tier": "SATURATED", "volume": 38, "diversity": 11, "avg_days_running": 47, "newest_entrant_days_ago": 2, "top_advertisers": ["The General", "Direct Auto", "State Farm"], "sample_hooks": ["My rate went up $400 with NO accidents"], "recommendation": "🟠 'pricing_pain' is crowded. Can still work with strong differentiation."},
        "loyalty_betrayal": {"score": 55.1, "tier": "SATURATED", "volume": 21, "diversity": 8, "avg_days_running": 63, "newest_entrant_days_ago": 5, "top_advertisers": ["Geico", "Progressive"], "sample_hooks": ["12 years, zero claims — they raised my rate anyway"], "recommendation": "🟠 'loyalty_betrayal' is crowded."},
        "geo_personalization": {"score": 31.2, "tier": "WARMING", "volume": 12, "diversity": 4, "avg_days_running": 112, "newest_entrant_days_ago": 8, "top_advertisers": ["usautoinsurancenow.com"], "sample_hooks": ["Notice for Wilmington, DE drivers who drive under 30 miles/day"], "recommendation": "🟡 'geo_personalization' is gaining traction."},
        "savings_discovery": {"score": 48.3, "tier": "WARMING", "volume": 18, "diversity": 6, "avg_days_running": 88, "newest_entrant_days_ago": 3, "top_advertisers": ["The Zebra", "Jerry"], "sample_hooks": ["I was overpaying by $67/month and didn't know it"], "recommendation": "🟡 'savings_discovery' is gaining traction."},
        "mechanism": {"score": 12.0, "tier": "FRESH", "volume": 4, "diversity": 2, "avg_days_running": 22, "newest_entrant_days_ago": 14, "top_advertisers": ["Root Insurance"], "sample_hooks": ["Insurance based on how YOU actually drive"], "recommendation": "🟢 Zero competition on 'mechanism'. First-mover advantage."},
        "loss_aversion": {"score": 8.5, "tier": "FRESH", "volume": 2, "diversity": 2, "avg_days_running": 18, "newest_entrant_days_ago": 18, "top_advertisers": [], "sample_hooks": [], "recommendation": "🟢 Zero competition on 'loss_aversion'. First-mover advantage."},
    }


def format_fatigue_for_discord(fatigue_results: dict[str, dict]) -> str:
    """Format fatigue report as a Discord-friendly string for embed fields."""
    lines = []
    tier_emoji = {"FRESH": "🟢", "WARMING": "🟡", "SATURATED": "🟠", "DEAD": "🔴"}

    for angle, data in fatigue_results.items():
        if data["volume"] == 0:
            continue
        emoji = tier_emoji.get(data["tier"], "⚪")
        lines.append(
            f"{emoji} **{angle}** — {data['tier']} (score: {data['score']}) "
            f"| {data['volume']} ads, {data['diversity']} advertisers"
        )
    return "\n".join(lines[:15]) or "No angle data available."


def format_fatigue_for_prompt(fatigue_results: dict[str, dict]) -> str:
    """Format fatigue data for injection into the generate.py LLM prompt."""
    sections = []
    for angle, data in fatigue_results.items():
        if data["volume"] == 0 and data["tier"] != "FRESH":
            continue
        sections.append(
            f"- {angle}: {data['tier']} (score {data['score']}) | "
            f"{data['volume']} competitors | avg {data['avg_days_running']}d running | "
            f"{data.get('recommendation', '')}"
        )
    return "ANGLE FATIGUE:\n" + "\n".join(sections)


if __name__ == "__main__":
    import yaml
    config_path = ROOT / "config" / "config.yaml"
    config = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}

    niche = config.get("offer", {}).get("niche", "auto_insurance")
    intel_ads = load_intel_from_baserow(config, niche)
    results = score_angle_fatigue(intel_ads, niche=niche)

    # Print recommendations
    print("\n=== RECOMMENDATIONS ===")
    for angle, data in results.items():
        if data.get("recommendation"):
            print(data["recommendation"])

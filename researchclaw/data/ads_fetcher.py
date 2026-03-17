"""Live ad creative data fetcher for ads mode.

Fetches real data from public sources before the LLM synthesis stages run.
This replaces LLM-hallucinated "candidates" with actual market intelligence.

Sources:
  - Facebook Ad Library (public, no auth required for basic search)
  - Reddit (public JSON API, no auth required)
  - Google Trends via pytrends (optional dependency)
  - DuckDuckGo search for Amazon reviews and competitor pages

All fetching is best-effort: each source failure is logged and skipped
gracefully. The pipeline never blocks on a fetch failure.

Public API
----------
- ``fetch_ads_intelligence(topic, platform, config) -> list[dict]``
  Returns a list of candidate dicts ready to write as candidates.jsonl
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Platform context strings injected into prompts
# ---------------------------------------------------------------------------

PLATFORM_CONTEXT: dict[str, dict[str, str]] = {
    "meta": {
        "name": "Meta (Facebook/Instagram)",
        "hook_length": "3-7 seconds / 15-25 words",
        "video_sweet_spot": "15-45 seconds",
        "primary_format": "video (Reels, Feed), static image, carousel",
        "audience_mindset": "passive scroll, entertainment mode, low purchase intent",
        "hook_types": "Pattern interrupt, Curiosity gap, Bold claim, Question, Negative hook",
        "creative_notes": (
            "Sound-on dominant. Native-looking content (UGC) outperforms polished ads. "
            "First 3 seconds must stop the scroll. Captions required for sound-off fallback."
        ),
        "targeting_note": "Broad targeting + creative-led. Let the algorithm find the audience.",
        "kill_threshold": "Kill if CTR <0.8% after $75 spend or CPA >3x target",
        "scale_signal": "CTR >1.5%, Hook Rate >30%, CPA <target with 5+ conversions",
    },
    "tiktok": {
        "name": "TikTok",
        "hook_length": "1-3 seconds / 5-10 words",
        "video_sweet_spot": "15-30 seconds",
        "primary_format": "vertical video only",
        "audience_mindset": "entertainment-first, trend-aware, skeptical of ads",
        "hook_types": "Pattern interrupt, Trend hook, Sound-first, POV, Duet-style",
        "creative_notes": (
            "Sound-on always. Trending audio lifts performance. "
            "Native look is mandatory — polished = ignored. "
            "Hook must use motion or sound in first 1 second."
        ),
        "targeting_note": "Algorithm-driven. Broad audiences. Spark Ads for organic amplification.",
        "kill_threshold": "Kill if VTR <15% after 1000 impressions",
        "scale_signal": "VTR >25%, CTR >1%, comments/shares ratio high",
    },
    "youtube": {
        "name": "YouTube",
        "hook_length": "5-15 seconds before skip option",
        "video_sweet_spot": "2-3 minutes (instream) or 15-30 seconds (Shorts)",
        "primary_format": "skippable instream, non-skippable 15s, Shorts",
        "audience_mindset": "higher purchase intent, research mode, willing to watch longer",
        "hook_types": "Story open, Question, Bold claim, Fear/urgency, Before-After",
        "creative_notes": (
            "First 5 seconds must deliver a reason to keep watching before the skip button appears. "
            "VSL format (long-form story) works well for considered purchases. "
            "Brand safety matters more here."
        ),
        "targeting_note": "Intent-based via keywords + in-market audiences",
        "kill_threshold": "Kill if View Rate <20% on skippable ads",
        "scale_signal": "View Rate >30%, CTR >0.5%, conversions tracking correctly",
    },
    "native": {
        "name": "Native (Taboola/Outbrain/Newsbreak)",
        "hook_length": "Headline: 60-80 characters",
        "video_sweet_spot": "Not primary format — image + headline drives clicks",
        "primary_format": "image + headline (sponsored content widget)",
        "audience_mindset": "content consumption mode, not purchase mode, curiosity-driven",
        "hook_types": "Curiosity gap, Teaser, Listicle (Top X...), Question, Shocking stat",
        "creative_notes": (
            "Always leads to an advertorial, never direct to product page. "
            "CTR target: 15-18%. Advertorial CTR to sale: 5-10%. "
            "Takes 4-6 weeks to reach profitability. Start broad, narrow down. "
            "Marcel Zutler kill rule: kill placement at 3x CPA target."
        ),
        "targeting_note": "Publisher-based. Start broad, kill underperforming publishers at 3x CPA.",
        "kill_threshold": "Kill placement at 3x CPA target. Kill campaign if CTR <10% after $300",
        "scale_signal": "CTR >15%, CPA at target for 3+ consecutive days",
    },
}

# Default fallback for unknown platforms
_DEFAULT_PLATFORM = PLATFORM_CONTEXT["meta"]


def get_platform_context(platform: str) -> dict[str, str]:
    """Return platform context dict for the given platform slug."""
    return PLATFORM_CONTEXT.get(platform.lower().strip(), _DEFAULT_PLATFORM)


def build_platform_block(platform: str) -> str:
    """Return a formatted platform context block for injection into prompts."""
    ctx = get_platform_context(platform)
    return (
        f"\n=== PLATFORM: {ctx['name']} ===\n"
        f"Hook length: {ctx['hook_length']}\n"
        f"Video sweet spot: {ctx['video_sweet_spot']}\n"
        f"Primary format: {ctx['primary_format']}\n"
        f"Audience mindset: {ctx['audience_mindset']}\n"
        f"Best hook types: {ctx['hook_types']}\n"
        f"Creative notes: {ctx['creative_notes']}\n"
        f"Targeting: {ctx['targeting_note']}\n"
        f"Kill threshold: {ctx['kill_threshold']}\n"
        f"Scale signal: {ctx['scale_signal']}\n"
        f"=== END PLATFORM ===\n"
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
_DEFAULT_TIMEOUT = 15


def _http_get(url: str, *, timeout: int = _DEFAULT_TIMEOUT, headers: dict[str, str] | None = None) -> str:
    """Fetch URL and return response body as string. Raises on error."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", _USER_AGENT)
    req.add_header("Accept", "application/json, text/html, */*")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Source: Reddit
# ---------------------------------------------------------------------------

def _build_reddit_queries(topic: str) -> list[tuple[str, str]]:
    """Build (subreddit, query) pairs from a topic string."""
    # Extract keywords from topic for subreddit guessing
    topic_lower = topic.lower()
    pairs: list[tuple[str, str]] = []

    # Universal queries against relevant subreddits
    if any(w in topic_lower for w in ["insurance", "auto", "car"]):
        pairs += [
            ("r/Insurance", topic),
            ("r/personalfinance", topic),
            ("r/cars", "insurance rates"),
            ("r/Insurance", "switched insurance saved money"),
        ]
    elif any(w in topic_lower for w in ["weight", "diet", "fitness", "supplement"]):
        pairs += [
            ("r/loseit", topic),
            ("r/fitness", topic),
            ("r/nutrition", "before and after"),
            ("r/weightloss", "what actually worked"),
        ]
    elif any(w in topic_lower for w in ["skin", "beauty", "acne", "wrinkle"]):
        pairs += [
            ("r/SkincareAddiction", topic),
            ("r/AsianBeauty", topic),
            ("r/30PlusSkinCare", "what changed everything"),
        ]
    elif any(w in topic_lower for w in ["finance", "money", "debt", "loan", "credit"]):
        pairs += [
            ("r/personalfinance", topic),
            ("r/debtfree", topic),
            ("r/povertyfinance", "wish I knew earlier"),
        ]
    else:
        # Generic fallback
        slug = re.sub(r"[^a-z0-9]", "", topic_lower[:20])
        pairs += [
            ("r/all", f"{topic} review"),
            ("r/all", f"{topic} problem"),
            ("r/all", f"switched to {topic}"),
        ]

    return pairs


def fetch_reddit(topic: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Fetch relevant Reddit posts for audience language mining."""
    results: list[dict[str, Any]] = []
    queries = _build_reddit_queries(topic)

    for i, (subreddit, query) in enumerate(queries[:4]):  # Max 4 queries
        if i > 0:
            time.sleep(1.5)  # Reddit rate limit
        try:
            encoded = urllib.parse.quote(query)
            sub_slug = subreddit.replace("r/", "")
            if sub_slug.lower() == "all":
                url = f"https://www.reddit.com/search.json?q={encoded}&sort=top&limit={limit}&t=year"
            else:
                url = f"https://www.reddit.com/r/{sub_slug}/search.json?q={encoded}&sort=top&restrict_sr=1&limit={limit}&t=year"

            raw = _http_get(url, headers={"Accept": "application/json"})
            data = json.loads(raw)

            posts = data.get("data", {}).get("children", [])
            for post in posts:
                p = post.get("data", {})
                title = p.get("title", "").strip()
                selftext = p.get("selftext", "").strip()
                if not title:
                    continue
                # Use title + first 300 chars of body as the "abstract"
                body_preview = selftext[:300].replace("\n", " ").strip() if selftext else ""
                abstract = f"{title}. {body_preview}".strip(". ") if body_preview else title

                results.append({
                    "id": f"reddit-{p.get('id', len(results))}",
                    "title": title,
                    "source": f"Reddit {subreddit}",
                    "source_type": "audience_language",
                    "url": f"https://reddit.com{p.get('permalink', '')}",
                    "year": 2024,
                    "abstract": abstract,
                    "upvotes": p.get("score", 0),
                    "num_comments": p.get("num_comments", 0),
                    "awareness_stage": "unaware_or_problem_aware",
                    "emotional_driver": "inferred_from_content",
                    "collected_at": _utcnow_iso(),
                })
            logger.info("Reddit %s %r → %d posts", subreddit, query, len(posts))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Reddit fetch failed for %r: %s", query, exc)

    return results


# ---------------------------------------------------------------------------
# Source: Facebook Ad Library (public search, no auth)
# ---------------------------------------------------------------------------

_FB_ACCESS_TOKEN = (
    "EAAkLZBkudbJsBQypE8lgS2Cr5HWYacXqbpJB1ODj9MQ7xKRzJD1FXbvFGN9qBcHYTCt42ZCiTvtOpuHlZByhK6ZBUxukyzZCp4vv5AdATWqlKp07BsYyzAVj5AmifmI7Wz10sK7FfTAl8BRhpeaqzn2ODuZChwUI9ZAGRpkPuRvKbuwatpMKyF27gL9XpQ0SPTP0usuZBoTgPAudaxiv32xpTSnl1SEIQLfvxXg4J344rMIySbzGCYE3"
)


def fetch_facebook_ad_library(topic: str, *, limit: int = 15) -> list[dict[str, Any]]:
    """
    Fetch competitor ads from the Facebook Ad Library API (authenticated).
    Falls back to DuckDuckGo proxy if the API token is unavailable.
    """
    results: list[dict[str, Any]] = []
    token = os.environ.get("FB_ACCESS_TOKEN", _FB_ACCESS_TOKEN)

    # ── Primary: official FB Ad Library API ───────────────────────────────
    if token:
        try:
            # Extract short keyword from topic for search
            keyword = " ".join(topic.split()[:6])
            api_url = (
                "https://graph.facebook.com/v21.0/ads_archive"
                f"?access_token={token}"
                f"&search_terms={urllib.parse.quote(keyword)}"
                "&ad_reached_countries=['US']"
                "&ad_active_status=ALL"
                "&fields=id,ad_creative_bodies,ad_creative_link_captions,"
                "ad_creative_link_descriptions,ad_creative_link_titles,"
                "page_name,ad_delivery_start_time,ad_delivery_stop_time"
                f"&limit={min(limit, 25)}"
            )
            raw = _http_get(api_url, timeout=15)
            data = json.loads(raw)
            ads = data.get("data", [])
            for i, ad in enumerate(ads[:limit]):
                bodies = ad.get("ad_creative_bodies") or []
                titles = ad.get("ad_creative_link_titles") or []
                descs = ad.get("ad_creative_link_descriptions") or []
                captions = ad.get("ad_creative_link_captions") or []
                text = " | ".join(filter(None, bodies + titles + descs + captions))
                if not text:
                    continue
                results.append({
                    "id": f"fb-api-{i}",
                    "title": titles[0] if titles else f"FB Ad — {ad.get('page_name', topic)}",
                    "source": "Facebook Ad Library API",
                    "source_type": "ad_creative",
                    "url": f"https://www.facebook.com/ads/library/?id={ad.get('id', '')}",
                    "year": 2025,
                    "abstract": text[:1000],
                    "page_name": ad.get("page_name", ""),
                    "awareness_stage": "product_aware_or_solution_aware",
                    "emotional_driver": "inferred",
                    "collected_at": _utcnow_iso(),
                })
            logger.info("FB Ad Library API %r → %d ads", keyword, len(results))
            if results:
                return results
        except Exception as exc:  # noqa: BLE001
            logger.warning("FB Ad Library API failed (%s) — falling back to DDG proxy", exc)

    # ── Fallback: DuckDuckGo proxy ─────────────────────────────────────────
    queries = [
        f'"{topic}" facebook ad hooks examples',
        f'"{topic}" competitor ads creative analysis',
    ]
    for i, query in enumerate(queries):
        if i > 0:
            time.sleep(1.0)
        try:
            encoded = urllib.parse.quote(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded}"
            raw = _http_get(url)
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', raw, re.DOTALL)
            titles_raw = re.findall(r'class="result__title"[^>]*>.*?<a[^>]*>(.*?)</a>', raw, re.DOTALL)
            titles_clean = [re.sub(r"<[^>]+>", "", t).strip() for t in titles_raw]
            for j, snippet in enumerate(snippets[:4]):
                clean = re.sub(r"<[^>]+>", "", snippet).strip()
                if len(clean) < 30:
                    continue
                results.append({
                    "id": f"fb-ddg-{i}-{j}",
                    "title": titles_clean[j] if j < len(titles_clean) else f"Competitor ad — {topic}",
                    "source": "Facebook Ad Library (via DDG proxy)",
                    "source_type": "ad_creative",
                    "url": f"https://www.facebook.com/ads/library/?q={urllib.parse.quote(topic)}",
                    "year": 2024,
                    "abstract": clean,
                    "awareness_stage": "product_aware_or_solution_aware",
                    "emotional_driver": "inferred",
                    "collected_at": _utcnow_iso(),
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("FB DDG proxy failed for %r: %s", query, exc)

    return results


# ---------------------------------------------------------------------------
# Source: Amazon Reviews (via DuckDuckGo → Amazon search result snippets)
# ---------------------------------------------------------------------------

def fetch_amazon_reviews(topic: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Fetch Amazon review snippets for audience language mining."""
    results: list[dict[str, Any]] = []

    queries = [
        f'site:amazon.com "{topic}" reviews "I was",  "finally", "wish I had"',
        f'site:amazon.com "{topic}" 5 star review "changed my"',
        f'site:amazon.com "{topic}" review "disappointed" OR "doesn\'t work"',
    ]

    for i, query in enumerate(queries[:2]):
        if i > 0:
            time.sleep(1.0)
        try:
            encoded = urllib.parse.quote(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded}"
            raw = _http_get(url)

            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', raw, re.DOTALL)

            for j, snippet in enumerate(snippets[:5]):
                clean = re.sub(r"<[^>]+>", "", snippet).strip()
                if len(clean) < 40:
                    continue
                # Tag as positive or negative based on query
                star_type = "5-star" if "5 star" in query or "changed" in query else "3-star"
                results.append({
                    "id": f"amazon-{i}-{j}",
                    "title": f"Amazon {star_type} review — {topic}",
                    "source": f"Amazon Reviews ({star_type})",
                    "source_type": "audience_language",
                    "url": f"https://www.amazon.com/s?k={urllib.parse.quote(topic)}&rh=p_72%3A1248963011",
                    "year": 2024,
                    "abstract": clean,
                    "awareness_stage": "most_aware" if "5 star" in query else "problem_aware",
                    "emotional_driver": "desired_outcome" if "5 star" in query else "pain_avoidance",
                    "collected_at": _utcnow_iso(),
                })
            logger.info("Amazon reviews %r → %d snippets", query, len(snippets))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Amazon review fetch failed for %r: %s", query, exc)

    return results


# ---------------------------------------------------------------------------
# Source: Google Trends proxy (via DuckDuckGo trend queries)
# ---------------------------------------------------------------------------

def fetch_trend_signals(topic: str) -> list[dict[str, Any]]:
    """Fetch trend signals — rising keywords, seasonal patterns, news hooks."""
    results: list[dict[str, Any]] = []

    queries = [
        f'"{topic}" trend 2024 2025 rising',
        f'"{topic}" news recent viral',
    ]

    for i, query in enumerate(queries):
        if i > 0:
            time.sleep(1.0)
        try:
            encoded = urllib.parse.quote(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded}"
            raw = _http_get(url)

            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', raw, re.DOTALL)
            titles_raw = re.findall(r'class="result__title"[^>]*>.*?<a[^>]*>(.*?)</a>', raw, re.DOTALL)
            titles = [re.sub(r"<[^>]+>", "", t).strip() for t in titles_raw]

            for j, snippet in enumerate(snippets[:3]):
                clean = re.sub(r"<[^>]+>", "", snippet).strip()
                if len(clean) < 30:
                    continue
                title = titles[j] if j < len(titles) else f"Trend signal — {topic}"
                results.append({
                    "id": f"trend-{i}-{j}",
                    "title": title,
                    "source": "Web trend signals",
                    "source_type": "market_data",
                    "url": "",
                    "year": 2025,
                    "abstract": clean,
                    "awareness_stage": "unaware_or_problem_aware",
                    "emotional_driver": "timeliness_urgency",
                    "collected_at": _utcnow_iso(),
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("Trend signal fetch failed for %r: %s", query, exc)

    return results


# ---------------------------------------------------------------------------
# Source: Competitor brand research
# ---------------------------------------------------------------------------

def fetch_competitor_intel(topic: str) -> list[dict[str, Any]]:
    """Search for competitor brands, their messaging, and ad angles."""
    results: list[dict[str, Any]] = []

    queries = [
        f'top brands "{topic}" advertising campaign 2024 2025',
        f'"{topic}" ad campaign viral successful',
        f'"{topic}" advertising angles marketing strategy',
    ]

    for i, query in enumerate(queries[:2]):
        if i > 0:
            time.sleep(1.0)
        try:
            encoded = urllib.parse.quote(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded}"
            raw = _http_get(url)

            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', raw, re.DOTALL)
            titles_raw = re.findall(r'class="result__title"[^>]*>.*?<a[^>]*>(.*?)</a>', raw, re.DOTALL)
            titles = [re.sub(r"<[^>]+>", "", t).strip() for t in titles_raw]

            for j, snippet in enumerate(snippets[:4]):
                clean = re.sub(r"<[^>]+>", "", snippet).strip()
                if len(clean) < 30:
                    continue
                title = titles[j] if j < len(titles) else f"Competitor intel — {topic}"
                results.append({
                    "id": f"comp-{i}-{j}",
                    "title": title,
                    "source": "Competitor research (web)",
                    "source_type": "competitor_intel",
                    "url": "",
                    "year": 2024,
                    "abstract": clean,
                    "awareness_stage": "solution_aware_or_product_aware",
                    "emotional_driver": "inferred",
                    "collected_at": _utcnow_iso(),
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("Competitor intel fetch failed for %r: %s", query, exc)

    return results


# ---------------------------------------------------------------------------
# Primary entry point
# ---------------------------------------------------------------------------

def fetch_ads_intelligence(
    topic: str,
    *,
    platform: str = "meta",
    use_web_fetch: bool = True,
) -> list[dict[str, Any]]:
    """Fetch live creative intelligence from all available sources.

    Returns a list of candidate dicts compatible with candidates.jsonl format.
    Each candidate includes source_type, awareness_stage, and emotional_driver
    fields that the ads.yaml prompts use for screening and synthesis.

    Parameters
    ----------
    topic:
        Research topic / offer / niche to research.
    platform:
        Target advertising platform (meta | tiktok | youtube | native).
        Used to tag platform context in results.
    use_web_fetch:
        Whether to make live web requests. Set False in tests or offline mode.
    """
    if not use_web_fetch:
        logger.info("Ads fetcher: web fetch disabled, returning empty list")
        return []

    all_candidates: list[dict[str, Any]] = []
    fetch_start = _utcnow_iso()

    # 1. Reddit — audience language (highest priority for verbatim copy)
    logger.info("Ads fetcher: fetching Reddit audience language...")
    reddit_results = fetch_reddit(topic, limit=8)
    all_candidates.extend(reddit_results)
    logger.info("Ads fetcher: Reddit → %d results", len(reddit_results))

    time.sleep(1.5)

    # 2. Amazon reviews — desire language + objections
    logger.info("Ads fetcher: fetching Amazon review snippets...")
    amazon_results = fetch_amazon_reviews(topic, limit=8)
    all_candidates.extend(amazon_results)
    logger.info("Ads fetcher: Amazon → %d results", len(amazon_results))

    time.sleep(1.0)

    # 3. Facebook Ad Library proxy — competitor ad angles
    logger.info("Ads fetcher: fetching competitor ad angles...")
    fb_results = fetch_facebook_ad_library(topic, limit=10)
    all_candidates.extend(fb_results)
    logger.info("Ads fetcher: FB Ad Library → %d results", len(fb_results))

    time.sleep(1.0)

    # 4. Competitor brand intel
    logger.info("Ads fetcher: fetching competitor intel...")
    comp_results = fetch_competitor_intel(topic)
    all_candidates.extend(comp_results)
    logger.info("Ads fetcher: Competitor intel → %d results", len(comp_results))

    time.sleep(1.0)

    # 5. Trend signals
    logger.info("Ads fetcher: fetching trend signals...")
    trend_results = fetch_trend_signals(topic)
    all_candidates.extend(trend_results)
    logger.info("Ads fetcher: Trend signals → %d results", len(trend_results))

    # Add platform context to each candidate's metadata
    for c in all_candidates:
        c["platform"] = platform
        c["fetch_session"] = fetch_start

    logger.info(
        "Ads fetcher: total candidates fetched = %d (platform=%s)",
        len(all_candidates),
        platform,
    )

    return all_candidates

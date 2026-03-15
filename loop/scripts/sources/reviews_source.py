"""
reviews_source.py — Mine customer reviews for raw buyer language.

Strategy:
  Amazon blocks direct scraping entirely. So we mine the richer sources:
  - Trustpilot   — competitor brand reviews, tons of emotional language
  - ConsumerAffairs — detailed complaints + praise, longer-form
  - BBB           — dispute/complaint language (objection goldmine)
  - Yelp          — local service reviews
  - SiteJabber    — e-commerce/insurance reviews

Why reviews beat almost everything else for creative research:
  - Verbatim buyer language — no filter, no polish
  - 1★ reviews = pain points, objections, triggers → hooks that agitate
  - 5★ reviews = desired outcomes, trigger events → hooks that inspire
  - 3★ reviews = nuanced truth ("good but I wish...")  → angle refinement
  - Real words people use about a category → your copy vocabulary

Output feeds the analyze step: angle labeling, hook extraction, persona cards.
"""

import requests

_API_URL = "https://api.tavily.com/search"

# Review sites by niche — each niche has its own best sources
_NICHE_REVIEW_QUERIES = {
    "auto_insurance": [
        # 1-star: pain / objection / trigger
        ('trustpilot.com', "auto insurance company reviews 1 star rate increase cancelled"),
        ('consumeraffairs.com', "auto insurance reviews complaints rate hike"),
        ('bbb.org', "auto insurance complaint rate increase dropped"),
        # 5-star: desired outcome / trigger event
        ('trustpilot.com', "auto insurance saved money switched best rate review"),
        ('consumeraffairs.com', "auto insurance happy customer claim paid fast review"),
        # 3-star: nuanced truth
        ('trustpilot.com', "auto insurance review 3 stars good but"),
    ],
    "dental_implants": [
        ('trustpilot.com', "dental implants review cost worth it experience"),
        ('consumeraffairs.com', "dental implants complaints pain recovery experience"),
        ('trustpilot.com', "dental implants 5 stars best decision life changing"),
        ('healthgrades.com', "dental implants patient review experience recovery"),
    ],
    "weight_loss": [
        ('trustpilot.com', "weight loss program review results before after"),
        ('consumeraffairs.com', "weight loss supplement review did it work"),
        ('trustpilot.com', "weight loss 5 stars finally worked transformation"),
        ('reddit.com', "weight loss program honest review what worked"),
    ],
    "medicare": [
        ('trustpilot.com', "medicare advantage plan review complaints coverage denied"),
        ('consumeraffairs.com', "medicare supplement review cost coverage experience"),
        ('bbb.org', "medicare plan complaint denied coverage confusion"),
    ],
    "debt_relief": [
        ('trustpilot.com', "debt relief company review experience settlement"),
        ('consumeraffairs.com', "debt consolidation review complaints fees"),
        ('bbb.org', "debt settlement complaint scam fees"),
        ('trustpilot.com', "debt relief 5 stars paid off debt life changing"),
    ],
    "home_insurance": [
        ('trustpilot.com', "home insurance review claim denied complaint"),
        ('consumeraffairs.com', "homeowners insurance complaint rate increase dropped"),
        ('trustpilot.com', "home insurance 5 stars claim paid fast great service"),
    ],
    "life_insurance": [
        ('trustpilot.com', "life insurance review term whole policy experience"),
        ('consumeraffairs.com', "life insurance complaint denied claim beneficiary"),
        ('trustpilot.com', "life insurance 5 stars easy approval peace of mind"),
    ],
}

_DEFAULT_REVIEW_QUERIES = [
    ('trustpilot.com', "{topic} reviews 1 star complaints frustrated"),
    ('trustpilot.com', "{topic} reviews 5 stars best experience"),
    ('consumeraffairs.com', "{topic} reviews complaints experience"),
]

_STAR_SIGNALS = {
    "1": ["complaint", "terrible", "worst", "awful", "scam", "fraud", "cancel", "increase", "denied", "refused", "horrible", "disgusting", "never again", "avoid"],
    "5": ["amazing", "excellent", "best", "love", "saved", "highly recommend", "life changing", "worth it", "so happy", "great service", "fast", "easy"],
    "3": ["good but", "ok but", "decent but", "average", "could be better", "mixed", "neutral"],
}


def _infer_star_rating(text: str) -> str:
    """Infer star rating from review content if not explicitly available."""
    text_lower = text.lower()
    for rating, signals in _STAR_SIGNALS.items():
        if any(s in text_lower for s in signals):
            return rating
    return "unknown"


def _infer_angle(title: str, content: str) -> str:
    """Rough angle label from review content."""
    combined = (title + " " + content).lower()
    if any(w in combined for w in ["rate", "premium", "price", "expensive", "increase", "hike", "overcharged"]):
        return "pricing_pain"
    if any(w in combined for w in ["cancel", "drop", "refused", "denied", "rejected"]):
        return "abandonment_fear"
    if any(w in combined for w in ["claim", "accident", "paid", "coverage"]):
        return "claim_experience"
    if any(w in combined for w in ["switch", "switched", "saved", "cheaper", "better rate"]):
        return "savings_discovery"
    if any(w in combined for w in ["loyal", "years", "long time", "decade", "always"]):
        return "loyalty_betrayal"
    if any(w in combined for w in ["easy", "quick", "fast", "simple", "online"]):
        return "ease_of_use"
    return ""


def fetch_reviews(
    config: dict,
    topic: str,
    platform: str = "general",
    limit: int = 20,
    dry_run: bool = False,
) -> list[dict]:
    """
    Mine customer reviews from Trustpilot, ConsumerAffairs, BBB, and others via Tavily.

    Config keys:
      - tavily.api_key: Tavily API key (same key, reuses existing config)
      - reviews.max_results_per_query: Results per query (default 4)
      - reviews.star_filter: "1", "5", "3", "all" (default "all")

    Returns list of dicts compatible with intel_ads Baserow schema:
      title, content, url, source, platform, topic, days_running=0,
      hook_type="review_[1|5|3]star", angle (inferred)
    """
    if dry_run:
        print("[REVIEWS] DRY RUN — returning mock review data")
        return [
            {
                "title": "Rate went up 40% with no accidents or claims",
                "content": "I've been a loyal customer for 12 years. Zero accidents, zero claims, clean record. My renewal came in and my rate jumped 40%. When I called they said 'market conditions.' I switched to a regional carrier and saved $92/month. The loyalty means nothing to these companies.",
                "url": "https://www.trustpilot.com/mock/auto-insurance-review-1",
                "source": "reviews_trustpilot",
                "platform": platform,
                "topic": topic,
                "days_running": 0,
                "hook_type": "review_1star",
                "angle": "loyalty_betrayal",
                "star_rating": "1",
            },
            {
                "title": "Switched and saved $800 a year — wish I did this sooner",
                "content": "After my rate jumped again I finally shopped around. Used a comparison site, got quotes from 6 companies in 10 minutes. Same coverage, $67/month less. Kicking myself for not doing this years ago. The whole process took 20 minutes.",
                "url": "https://www.trustpilot.com/mock/auto-insurance-review-2",
                "source": "reviews_trustpilot",
                "platform": platform,
                "topic": topic,
                "days_running": 0,
                "hook_type": "review_5star",
                "angle": "savings_discovery",
                "star_rating": "5",
            },
        ]

    tavily_cfg = config.get("tavily", {})
    api_key = tavily_cfg.get("api_key", "")
    max_per_query = config.get("reviews", {}).get("max_results_per_query", 4)
    star_filter = config.get("reviews", {}).get("star_filter", "all")

    if not api_key:
        print("[REVIEWS] No tavily.api_key configured — skipping")
        return []

    # Pick query set
    niche = config.get("offer", {}).get("niche", "").lower().replace(" ", "_")
    topic_key = topic.lower().replace(" ", "_")

    query_pairs = (
        _NICHE_REVIEW_QUERIES.get(niche)
        or _NICHE_REVIEW_QUERIES.get(topic_key)
        or [(d.format(topic=topic), d) for d in _DEFAULT_REVIEW_QUERIES]
    )

    results = []
    seen_urls = set()

    for domain, query in query_pairs:
        if len(results) >= limit:
            break
        try:
            r = requests.post(
                _API_URL,
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "advanced",
                    "include_answer": False,
                    "max_results": max_per_query,
                    "include_domains": [domain],
                },
                timeout=15,
            )

            if r.status_code == 401:
                print("[REVIEWS] Invalid Tavily API key")
                return results
            if r.status_code == 429:
                print("[REVIEWS] Tavily rate limited — stopping early")
                break
            if r.status_code != 200:
                print(f"[REVIEWS] Query failed ({r.status_code}): {query[:50]}")
                continue

            for item in r.json().get("results", []):
                url = item.get("url", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                content = item.get("content", "").strip()
                title = item.get("title", "").strip()
                if len(content) < 80:
                    continue

                star_rating = _infer_star_rating(content)

                # Apply star filter if set
                if star_filter != "all" and star_rating != star_filter:
                    continue

                hook_type = f"review_{star_rating}star" if star_rating != "unknown" else "review"
                angle = _infer_angle(title, content)
                source_label = f"reviews_{domain.split('.')[0]}"

                results.append({
                    "title": title[:255],
                    "content": content[:3000],
                    "url": url,
                    "source": source_label,
                    "platform": platform,
                    "topic": topic,
                    "days_running": 0,
                    "hook_type": hook_type,
                    "angle": angle,
                    "star_rating": star_rating,
                    "sub_text": "",
                    "landing_url": url,
                    "ad_networks": [],
                    "geos": ["US"],
                    "ad_strength": 0,
                })

                if len(results) >= limit:
                    break

        except Exception as e:
            print(f"[REVIEWS] Error on '{query[:40]}': {e}")
            continue

    print(f"[REVIEWS] Fetched {len(results)} reviews for '{topic}'")
    return results

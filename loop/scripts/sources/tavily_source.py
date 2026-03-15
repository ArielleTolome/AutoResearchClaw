"""
tavily_source.py — Mine audience language from Reddit, forums, and reviews via Tavily Search.

Why Tavily over direct Reddit API:
  - No OAuth flow, no rate limits, no CAPTCHA
  - Searches Reddit + news + forums in one call
  - Returns structured content blocks ready for audience language extraction
  - advanced search depth pulls full thread content, not just titles

Use case: extract raw verbatim language that real buyers use —
complaints, trigger events, objections, desired outcomes.
This feeds the persona builder and hook generator in the analyze step.
"""

import requests
from datetime import datetime, timezone

_API_URL = "https://api.tavily.com/search"

# Subreddit/forum queries per niche — expand as needed
_NICHE_QUERIES = {
    "auto_insurance": [
        "site:reddit.com auto insurance too expensive rate increase",
        "site:reddit.com auto insurance cancelled dropped complaint",
        "site:reddit.com why is my car insurance so high",
        "site:reddit.com switched auto insurance saved money",
        "site:reddit.com auto insurance claim denied frustrating",
    ],
    "dental_implants": [
        "site:reddit.com dental implants cost too expensive worth it",
        "site:reddit.com dental implants experience before after",
        "site:reddit.com dental implants financing insurance wont cover",
    ],
    "weight_loss": [
        "site:reddit.com weight loss what actually worked for me",
        "site:reddit.com tried everything cant lose weight frustrated",
        "site:reddit.com weight loss before after transformation",
    ],
    "medicare": [
        "site:reddit.com medicare advantage plan confusion complaints",
        "site:reddit.com medicare supplement costs too high",
        "site:reddit.com medicare coverage gaps denied",
    ],
    "personal_finance": [
        "site:reddit.com debt payoff breakthrough moment",
        "site:reddit.com financial anxiety money stress",
    ],
}

_DEFAULT_QUERIES = [
    "site:reddit.com {topic} complaints frustrating experience",
    "site:reddit.com {topic} what actually worked",
    "site:reddit.com {topic} advice switching saving money",
]


def fetch_tavily_audience(
    config: dict,
    topic: str,
    platform: str = "general",
    limit: int = 20,
    dry_run: bool = False,
) -> list[dict]:
    """
    Mine Reddit and forum content via Tavily for audience language.

    Config keys:
      - tavily.api_key: Tavily API key
      - tavily.include_news: Also search news sites (default False)
      - tavily.max_results_per_query: Results per query call (default 5)

    Returns list of dicts with:
      title, content (raw audience language), url, source, topic,
      days_running (always 0 — not ad data), hook_type (always "audience_language")
    """
    if dry_run:
        print("[TAVILY] DRY RUN — returning mock audience language")
        return [
            {
                "title": "Why is my auto insurance so high despite clean record?",
                "content": "I've been with the same company for 8 years, never filed a claim, clean record, and my rate just jumped 40%. When I called they said it was 'market conditions'. I'm switching immediately.",
                "url": "https://reddit.com/r/Insurance/mock1",
                "source": "tavily_reddit",
                "platform": platform,
                "topic": topic,
                "days_running": 0,
                "hook_type": "audience_language",
                "angle": "loyalty_betrayal",
            },
            {
                "title": "Finally switched and saved $800/year",
                "content": "Switched from State Farm to a smaller regional carrier. Same coverage, $67/month savings. The trick was using a broker who shops multiple carriers at once.",
                "url": "https://reddit.com/r/Insurance/mock2",
                "source": "tavily_reddit",
                "platform": platform,
                "topic": topic,
                "days_running": 0,
                "hook_type": "audience_language",
                "angle": "savings_discovery",
            },
        ]

    tavily_cfg = config.get("tavily", {})
    api_key = tavily_cfg.get("api_key", "")
    max_per_query = tavily_cfg.get("max_results_per_query", 5)
    include_news = tavily_cfg.get("include_news", False)

    if not api_key:
        print("[TAVILY] No api_key configured — skipping")
        return []

    # Pick query set: niche-specific or default
    niche = config.get("offer", {}).get("niche", "").lower().replace(" ", "_")
    topic_lower = topic.lower().replace(" ", "_")

    queries = _NICHE_QUERIES.get(niche) or _NICHE_QUERIES.get(topic_lower)
    if not queries:
        queries = [q.format(topic=topic) for q in _DEFAULT_QUERIES]

    include_domains = ["reddit.com"]
    if include_news:
        include_domains += ["quora.com", "trustpilot.com", "consumeraffairs.com"]

    results = []
    seen_urls = set()

    for query in queries:
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
                    "include_domains": include_domains,
                },
                timeout=15,
            )

            if r.status_code == 401:
                print("[TAVILY] Invalid API key")
                return results

            if r.status_code == 429:
                print("[TAVILY] Rate limited — stopping early")
                break

            if r.status_code != 200:
                print(f"[TAVILY] Query failed ({r.status_code}): {query[:50]}")
                continue

            for item in r.json().get("results", []):
                url = item.get("url", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                content = item.get("content", "").strip()
                if len(content) < 50:
                    continue

                results.append({
                    "title": item.get("title", "")[:255],
                    "content": content[:3000],
                    "url": url,
                    "source": "tavily_reddit",
                    "platform": platform,
                    "topic": topic,
                    "days_running": 0,
                    "hook_type": "audience_language",
                    "angle": "",  # filled by analyze step
                    "sub_text": "",
                    "landing_url": url,
                    "ad_networks": [],
                    "geos": ["US"],
                    "ad_strength": 0,
                })

                if len(results) >= limit:
                    break

        except Exception as e:
            print(f"[TAVILY] Error on query '{query[:40]}': {e}")
            continue

    print(f"[TAVILY] Fetched {len(results)} audience language snippets for '{topic}'")
    return results

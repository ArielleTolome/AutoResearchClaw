#!/usr/bin/env python3
from __future__ import annotations
"""
news_watcher.py — Continuous news intelligence for AutoResearchClaw
Watches Google News RSS for leadgen verticals + major players,
scores each article with Claude, writes new rows to Baserow table 767,
and fires LEADGEN_INDUSTRY_NEWS signals to Discord.

Usage:
  python3 news_watcher.py                  # Run once (designed for cron)
  python3 news_watcher.py --dry-run        # Score articles, don't write to Baserow
  python3 news_watcher.py --verticals-only # Skip company-specific searches
  python3 news_watcher.py --limit 5        # Cap articles per search query

Config (env or auto-detected from config.yaml):
  ANTHROPIC_API_KEY     — Claude API key
  BASEROW_TOKEN         — Baserow API token
  DISCORD_WEBHOOK_URL   — #creative-signals webhook
  BASEROW_URL           — default: https://baserow.pfsend.com
  NEWS_TABLE_ID         — industry news table (default: 767)
  SENTIMENT_TABLE_ID    — vertical sentiment table (default: 768)
  PLAYERS_TABLE_ID      — major players table (default: 769)
"""

import os, sys, json, re, time, hashlib, argparse, datetime, xml.etree.ElementTree as ET, subprocess
from pathlib import Path

import requests

# ── Qdrant (conditional) ────────────────────────────────────────────────────
try:
    from qdrant_sink import upsert_signal as _qdrant_upsert
except Exception:
    _qdrant_upsert = None

# ── Config ───────────────────────────────────────────────────────────────────
BASEROW_URL        = os.getenv("BASEROW_URL", "https://baserow.pfsend.com")
BASEROW_TOKEN      = os.getenv("BASEROW_TOKEN", "Yg1DSyEipxerKG9cuVJg6p6OjN0RTN4R")
DISCORD_WEBHOOK    = os.getenv("DISCORD_WEBHOOK_URL", "")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
NEWS_TABLE_ID      = int(os.getenv("NEWS_TABLE_ID", "767"))
SENTIMENT_TABLE_ID = int(os.getenv("SENTIMENT_TABLE_ID", "768"))
PLAYERS_TABLE_ID   = int(os.getenv("PLAYERS_TABLE_ID", "769"))
SEEN_FILE          = Path(os.getenv("SEEN_FILE",
    Path(__file__).parent.parent / "config" / ".news_seen.json"))

# Auto-load API key from config.yaml if not set
if not ANTHROPIC_API_KEY:
    cfg_path = Path(__file__).parent.parent / "config" / "config.yaml"
    if cfg_path.exists():
        for line in cfg_path.read_text().splitlines():
            if "api_key" in line and "anthropic" not in line.lower():
                pass
            m = re.search(r'api_key:\s*["\']?([A-Za-z0-9\-_]+)["\']?', line)
            if m and m.group(1) not in ("YOUR_ANTHROPIC_API_KEY", "YOUR_KEY"):
                ANTHROPIC_API_KEY = m.group(1)
                break

# Auto-load Discord webhook from config.yaml if not set
if not DISCORD_WEBHOOK:
    cfg_path = Path(__file__).parent.parent / "config" / "config.yaml"
    if cfg_path.exists():
        for line in cfg_path.read_text().splitlines():
            m = re.search(r'webhook_url:\s*["\']?(https://discord\.com/api/webhooks/[^"\']+)["\']?', line)
            if m:
                DISCORD_WEBHOOK = m.group(1)
                break

# ── Search queries ───────────────────────────────────────────────────────────
VERTICAL_QUERIES = [
    ("Auto Insurance",       "auto insurance leads market 2026"),
    ("Home Insurance",       "home insurance leads market 2026"),
    ("Medicare",             "Medicare Advantage leads market 2026"),
    ("Final Expense",        "final expense insurance leads market 2026"),
    ("ACA",                  "ACA health insurance marketplace 2026"),
    ("U65 Private Health",   "under 65 health insurance leads 2026"),
    ("Debt Settlement",      "debt settlement leads market 2026"),
    ("Tax Settlement",       "tax resolution leads IRS 2026"),
    ("Home Services",        "home services leads market 2026"),
    ("Personal Injury",      "personal injury leads legal 2026"),
    ("Mass Tort",            "mass tort litigation leads 2026"),
    ("Home Services",        "solar leads roofing leads 2026"),
]

# Key companies to track (pulled dynamically from table 769 too)
COMPANY_QUERIES = [
    "Progressive Insurance news 2026",
    "GEICO news 2026",
    "Allstate news 2026",
    "State Farm news 2026",
    "Medicare Advantage CMS 2026",
    "LendingTree leads news 2026",
    "QuoteWizard news 2026",
    "EverQuote news 2026",
    "SelectQuote news 2026",
]

# ── Seen-hash tracking ───────────────────────────────────────────────────────

def load_seen():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()

def save_seen(seen: set):
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(list(seen)))

def article_hash(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12]

# ── Google News RSS ──────────────────────────────────────────────────────────

def fetch_google_news(query: str, limit: int = 5):
    """Fetch articles from Google News RSS for a query."""
    q = query.replace(" ", "+")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "AutoResearchClaw/1.7"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
        items = []
        for item in root.findall(".//item")[:limit]:
            title   = item.findtext("title", "").strip()
            link    = item.findtext("link", "").strip()
            pub     = item.findtext("pubDate", "").strip()
            source  = item.findtext("source", "").strip()
            desc    = re.sub(r"<[^>]+>", "", item.findtext("description", "")).strip()
            # Parse pub date
            try:
                dt = datetime.datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z")
                pub_iso = dt.strftime("%Y-%m-%d")
            except Exception:
                pub_iso = datetime.date.today().isoformat()
            items.append({
                "title": title, "url": link, "pub_date": pub_iso,
                "source": source, "description": desc,
            })
        return items
    except Exception as e:
        print(f"[WARN] RSS fetch failed for '{query}': {e}")
        return []

# ── Claude scoring ───────────────────────────────────────────────────────────

SCORE_PROMPT_TEMPLATE = """Score this article for US leadgen relevance. Respond with ONLY a JSON object, nothing else.

JSON schema:
{{
  "relevant": true or false,
  "vertical": "one of: Auto Insurance | Home Insurance | Medicare | Final Expense | ACA | U65 Private Health | Debt Settlement | Tax Settlement | Home Services | Personal Injury | Mass Tort | General Leadgen | Other",
  "impact_type": "one of: Regulatory/Government | Market/Competitive | Financial | Technology | Consumer Behavior",
  "sentiment_impact": "one of: Very Bullish | Bullish | Neutral | Bearish | Very Bearish",
  "headline": "cleaned headline string",
  "summary": "2-3 sentences: what happened and why it matters for leadgen buyers/sellers",
  "action_items": "numbered list of 2-4 concrete actions for a leadgen operator",
  "companies_affected": "comma-separated company names, or empty string"
}}

Only mark relevant=true if this directly affects lead pricing, buyer demand, regulation, or audience behavior in US leadgen.

Article:
Headline: {title}
Source: {source}
Date: {pub_date}
Description: {description}"""


def score_article(article: dict) -> dict | None:
    """Score article using Codex CLI (ChatGPT Pro OAuth). Returns enriched dict or None if not relevant."""
    prompt = SCORE_PROMPT_TEMPLATE.format(
        title=article["title"],
        source=article["source"] or "Unknown",
        pub_date=article["pub_date"],
        description=article["description"][:500],
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
        # Extract last JSON object from output (codex may echo prompt + response)
        json_matches = re.findall(r'\{[^{}]+\}', output, re.DOTALL)
        if not json_matches:
            print(f"  [WARN] No JSON in codex output")
            return None
        # Take the last match (the response, not any echo)
        raw = json_matches[-1]
        scored = json.loads(raw)
        if not scored.get("relevant"):
            return None
        scored.update({
            "source_url": article["url"],
            "source":     article["source"] or "Google News",
            "pub_date":   article["pub_date"],
        })
        return scored
    except subprocess.TimeoutExpired:
        print(f"  [WARN] Codex scoring timed out")
        return None
    except Exception as e:
        print(f"  [WARN] Codex scoring failed: {e}")
        return None

# ── Baserow write ─────────────────────────────────────────────────────────────

def get_field_options(table_id: int):
    """Fetch field metadata to get option IDs for single-select fields."""
    url = f"{BASEROW_URL}/api/database/fields/table/{table_id}/"
    r = requests.get(url, headers={"Authorization": f"Token {BASEROW_TOKEN}"}, timeout=10)
    r.raise_for_status()
    fields = r.json()
    options = {}
    for field in fields:
        if field.get("type") in ("single_select", "link_row"):
            opts = {o["value"]: o["id"] for o in field.get("select_options", [])}
            options[field["name"]] = opts
    return options

def write_news_row(scored: dict, field_options: dict, dry_run=False) -> int | None:
    """Write a scored article to Baserow table 767. Returns row ID or None."""
    def opt_id(field, value):
        return {"id": field_options.get(field, {}).get(value)} if value else None

    payload = {
        "Headline":        scored.get("headline", "")[:255],
        "Source URL":      scored.get("source_url", ""),
        "Source":          scored.get("source", ""),
        "Published Date":  scored.get("pub_date", ""),
        "Summary":         scored.get("summary", ""),
        "Action Items":    scored.get("action_items", ""),
        "Companies Affected": scored.get("companies_affected", ""),
        "Active":          True,
        "Vertical":        opt_id("Vertical", scored.get("vertical")),
        "Impact Type":     opt_id("Impact Type", scored.get("impact_type")),
        "Sentiment Impact": opt_id("Sentiment Impact", scored.get("sentiment_impact")),
    }
    # Remove None values
    payload = {k: v for k, v in payload.items() if v is not None}

    if dry_run:
        print(f"  [DRY-RUN] Would write: {scored.get('headline','')[:80]}")
        return None

    url = f"{BASEROW_URL}/api/database/rows/table/{NEWS_TABLE_ID}/?user_field_names=true"
    r = requests.post(url,
        headers={"Authorization": f"Token {BASEROW_TOKEN}", "Content-Type": "application/json"},
        json=payload, timeout=10)
    if r.status_code in (200, 201):
        row_id = r.json().get("id")
        print(f"  [BASEROW] Row {row_id} written: {scored.get('headline','')[:60]}")
        return row_id
    else:
        print(f"  [ERR] Baserow write failed {r.status_code}: {r.text[:200]}")
        return None

# ── Discord signal ────────────────────────────────────────────────────────────

SENTIMENT_EMOJI = {
    "Very Bullish": "🚀", "Bullish": "🟢", "Neutral": "🟡",
    "Bearish": "🔴", "Very Bearish": "💀",
}
IMPACT_COLOR = {
    "Regulatory/Government": 0xe74c3c,
    "Market/Competitive":    0xe67e22,
    "Financial":             0x3498db,
    "Technology":            0x9b59b6,
    "Consumer Behavior":     0x2ecc71,
}

def fire_discord_signal(scored: dict):
    if not DISCORD_WEBHOOK:
        print("  [WARN] No Discord webhook — skipping signal")
        return
    impact    = scored.get("impact_type", "")
    sentiment = scored.get("sentiment_impact", "Neutral")
    vertical  = scored.get("vertical", "")
    headline  = scored.get("headline", "")
    source    = scored.get("source", "")
    source_url = scored.get("source_url", "")
    summary   = scored.get("summary", "")
    actions   = scored.get("action_items", "")
    companies = scored.get("companies_affected", "")

    fields = []
    if summary:
        fields.append({"name": "📋 Summary", "value": summary[:1024], "inline": False})
    if actions:
        fields.append({"name": "⚡ Action Items", "value": actions[:1024], "inline": False})
    if companies:
        fields.append({"name": "🏢 Companies", "value": companies[:512], "inline": True})
    fields.append({"name": "📅 Published", "value": scored.get("pub_date", ""), "inline": True})

    embed = {
        "title": f"📰 LEADGEN_INDUSTRY_NEWS — {vertical}",
        "description": f"**{headline}**\n[{source}]({source_url})" if source_url else f"**{headline}**",
        "color": IMPACT_COLOR.get(impact, 0x95a5a6),
        "fields": fields,
        "footer": {"text": f"{SENTIMENT_EMOJI.get(sentiment,'⚪')} {sentiment}  |  {impact}  |  AutoResearchClaw news_watcher"},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    r = requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
    if r.status_code in (200, 204):
        print(f"  [DISCORD] Signal fired: {headline[:60]}")
    else:
        print(f"  [ERR] Discord {r.status_code}: {r.text[:100]}")

# ── Main loop ─────────────────────────────────────────────────────────────────

def run(args):
    seen = load_seen()
    field_options = {}

    if not args.dry_run:
        try:
            field_options = get_field_options(NEWS_TABLE_ID)
            print(f"[init] Loaded field options for table {NEWS_TABLE_ID}")
        except Exception as e:
            print(f"[WARN] Could not load field options: {e}")

    queries = []
    if not args.companies_only:
        queries += [(v, q) for v, q in VERTICAL_QUERIES]
    if not args.verticals_only:
        queries += [("Company", q) for q in COMPANY_QUERIES]

    # Also pull active companies from Baserow table 769
    if not args.verticals_only:
        try:
            r = requests.get(
                f"{BASEROW_URL}/api/database/rows/table/{PLAYERS_TABLE_ID}/?user_field_names=true&size=200",
                headers={"Authorization": f"Token {BASEROW_TOKEN}"}, timeout=10)
            players = r.json().get("results", [])
            active  = [p for p in players if p.get("Lead Buying Status", {}).get("value") == "Active Buyer"]
            for p in active[:20]:  # cap to top 20
                company = p.get("Company", "")
                if company:
                    queries.append(("Company", f"{company} news 2026"))
            print(f"[init] Added {min(len(active),20)} active buyer queries from Baserow")
        except Exception as e:
            print(f"[WARN] Could not load players table: {e}")

    print(f"\n[watch] Running {len(queries)} search queries (limit={args.limit} each)\n")

    total_new = 0
    for vertical, query in queries:
        articles = fetch_google_news(query, limit=args.limit)
        new_count = 0
        for article in articles:
            h = article_hash(article["url"])
            if h in seen:
                continue
            seen.add(h)

            print(f"  [score] {article['title'][:70]}")
            scored = score_article(article)
            if scored is None:
                print(f"  [skip] Not relevant")
                time.sleep(0.3)
                continue

            print(f"  [+] {scored.get('sentiment_impact')} | {scored.get('impact_type')} | {scored.get('vertical')}")

            row_id = write_news_row(scored, field_options, dry_run=args.dry_run)
            if row_id or args.dry_run:
                fire_discord_signal(scored)
                # Qdrant vectorization (best-effort)
                if _qdrant_upsert and not args.dry_run:
                    _qdrant_upsert({**scored, "signal_type": "news"})
                new_count += 1
                total_new += 1

            time.sleep(0.5)  # rate limit

        if new_count:
            print(f"  → {new_count} new articles for '{vertical}'\n")

    if not args.dry_run:
        save_seen(seen)

    print(f"\n✅ Done — {total_new} new articles written to Baserow + signaled to Discord")

def main():
    parser = argparse.ArgumentParser(description="AutoResearchClaw news watcher")
    parser.add_argument("--dry-run",        action="store_true", help="Don't write to Baserow")
    parser.add_argument("--verticals-only", action="store_true", help="Skip company searches")
    parser.add_argument("--companies-only", action="store_true", help="Skip vertical searches")
    parser.add_argument("--limit",          type=int, default=5, help="Articles per query (default 5)")
    args = parser.parse_args()
    run(args)

if __name__ == "__main__":
    main()

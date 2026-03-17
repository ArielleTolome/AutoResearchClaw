#!/usr/bin/env python3
from __future__ import annotations
"""
reddit_source.py — Reddit audience language miner for AutoResearchClaw
Fetches hot/top posts from leadgen subreddits, scores with Codex CLI,
writes to Baserow table 768, fires Discord embeds to #intel.

Usage:
  python reddit_source.py [--dry-run] [--limit N] [--subreddit NAME]
"""

import os, sys, json, re, time, hashlib, argparse, datetime, subprocess
from pathlib import Path

import requests
import yaml

# ── Qdrant (conditional) ────────────────────────────────────────────────────
try:
    from qdrant_sink import upsert_signal as _qdrant_upsert
except Exception:
    _qdrant_upsert = None

# ── Config ───────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"
STATE_DIR = Path(__file__).parent.parent / "state"
SEEN_PATH = STATE_DIR / "reddit_seen.json"

def load_config() -> dict:
    if CFG_PATH.exists():
        return yaml.safe_load(CFG_PATH.read_text()) or {}
    return {}

CFG = load_config()
BASEROW_KEY       = CFG.get("baserow", {}).get("api_key", os.getenv("BASEROW_TOKEN", "Yg1DSyEipxerKG9cuVJg6p6OjN0RTN4R"))
BASEROW_BASE_URL  = CFG.get("baserow", {}).get("url", "https://baserow.pfsend.com")
INTEL_WEBHOOK     = CFG.get("discord", {}).get("intel_webhook_url", os.getenv("INTEL_WEBHOOK_URL", ""))
TAVILY_KEY        = CFG.get("tavily", {}).get("api_key", os.getenv("TAVILY_API_KEY", "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"))
BASEROW_TABLE     = 818  # reddit_audience_signals
BASEROW_URL       = f"{BASEROW_BASE_URL}/api/database/rows/table/{BASEROW_TABLE}/?user_field_names=true"
REDDIT_UA         = "Mozilla/5.0 (compatible; AutoResearchClaw/1.9; +https://github.com/ArielleTolome/AutoResearchClaw)"

SUBREDDITS = [
    "InsuranceAgent",
    "legaladvice",
    "personalfinance",
    "Medicare",
    "HealthInsurance",
    "AutoInsurance",
    "DebtFree",
]

EMOTION_COLORS = {
    "Frustrated": 0xE74C3C,
    "Confused":   0xE67E22,
    "Anxious":    0xF1C40F,
    "Hopeful":    0x2ECC71,
    "Relieved":   0x1ABC9C,
    "Angry":      0x8B0000,
    "Neutral":    0x95A5A6,
}

SCORE_PROMPT = """Score this Reddit post for US leadgen audience research. Respond with ONLY a JSON object, nothing else.

JSON schema:
{{
  "relevant": true or false,
  "verbatim_hook": "the single most quotable sentence from this post that could become an ad hook — exact words",
  "pain_point": "1 sentence summary of the core pain or desire",
  "emotion": "one of: Frustrated | Confused | Angry | Relieved | Hopeful | Anxious | Neutral",
  "vertical": "one of: Auto Insurance | Home Insurance | Medicare | Final Expense | ACA | U65 Private Health | Debt Settlement | Tax Settlement | Home Services | Personal Injury | Mass Tort | General Finance | Other",
  "awareness_level": "one of: Unaware | Problem Aware | Solution Aware | Product Aware | Most Aware"
}}

Only mark relevant=true if this expresses a real pain point, desire, or struggle related to insurance, debt, legal issues, or personal finance.

Post:
Subreddit: r/{subreddit}
Title: {title}
Body: {body}"""


# ── State management ─────────────────────────────────────────────────────────
def load_seen() -> set:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if SEEN_PATH.exists():
        return set(json.loads(SEEN_PATH.read_text()))
    return set()

def save_seen(seen: set):
    SEEN_PATH.write_text(json.dumps(list(seen)))


# ── Reddit fetch via Tavily ───────────────────────────────────────────────────
def fetch_posts(subreddit: str, limit: int = 25) -> list[dict]:
    """Use Tavily search with site:reddit.com/r/{sub} to bypass IP blocks."""
    posts = []
    if not TAVILY_KEY:
        print(f"  [WARN] No TAVILY_API_KEY — cannot fetch r/{subreddit}")
        return posts

    queries = [
        f"site:reddit.com/r/{subreddit} insurance OR debt OR claim OR premium OR coverage",
        f"site:reddit.com/r/{subreddit} help OR advice OR problem OR frustrated OR confused",
    ]

    for query in queries:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key":        TAVILY_KEY,
                    "query":          query,
                    "search_depth":   "basic",
                    "max_results":    limit // 2,
                    "include_answer": False,
                },
                timeout=20,
            )
            r.raise_for_status()
            for result in r.json().get("results", []):
                url = result.get("url", "")
                if "reddit.com/r/" not in url:
                    continue
                posts.append({
                    "title":        result.get("title", "")[:300],
                    "body":         result.get("content", "")[:500],
                    "score":        0,
                    "num_comments": 0,
                    "url":          url,
                    "subreddit":    subreddit,
                    "created_utc":  0,
                })
            time.sleep(0.5)
        except Exception as e:
            print(f"  [WARN] Tavily fetch failed for r/{subreddit}: {e}")

    # dedupe within batch by url
    seen_urls: set = set()
    unique = []
    for p in posts:
        if p["url"] not in seen_urls:
            seen_urls.add(p["url"])
            unique.append(p)
    return unique


# ── Scoring via Codex CLI ─────────────────────────────────────────────────────
def score_post(post: dict) -> dict | None:
    prompt = SCORE_PROMPT.format(
        subreddit=post["subreddit"],
        title=post["title"],
        body=post["body"] or "(no body)",
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
        if not scored.get("relevant"):
            return None
        return scored
    except subprocess.TimeoutExpired:
        print(f"  [WARN] Codex timeout on post: {post['title'][:50]}")
        return None
    except Exception as e:
        print(f"  [WARN] Scoring failed: {e}")
        return None


# ── Baserow write ─────────────────────────────────────────────────────────────
def write_to_baserow(post: dict, scored: dict, dry_run: bool) -> bool:
    row = {
        "subreddit":      post["subreddit"],
        "title":          post["title"][:255],
        "pain_point":     scored.get("pain_point", "")[:500],
        "verbatim_hook":  scored.get("verbatim_hook", "")[:500],
        "emotion":        scored.get("emotion", "Neutral"),
        "vertical":       scored.get("vertical", "Other"),
        "awareness_level": scored.get("awareness_level", ""),
        "score":          post["score"],
        "num_comments":   post["num_comments"],
        "url":            post["url"],
        "created_date":   datetime.datetime.fromtimestamp(post["created_utc"], datetime.timezone.utc).strftime("%Y-%m-%d"),
    }
    if dry_run:
        print(f"    [DRY-RUN] Would write to Baserow: {row['title'][:60]}")
        return True
    try:
        r = requests.post(
            BASEROW_URL,
            headers={"Authorization": f"Token {BASEROW_KEY}", "Content-Type": "application/json"},
            json=row,
            timeout=15,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"  [WARN] Baserow write failed: {e}")
        return False


# ── Discord embed ─────────────────────────────────────────────────────────────
def fire_discord(post: dict, scored: dict, dry_run: bool):
    if not INTEL_WEBHOOK:
        print("  [WARN] No intel_webhook_url configured — skipping Discord")
        return
    hook = (scored.get("verbatim_hook") or post["title"])[:256]
    color = EMOTION_COLORS.get(scored.get("emotion", "Neutral"), 0x95A5A6)
    embed = {
        "title":       hook,
        "description": scored.get("pain_point", "")[:500],
        "color":       color,
        "footer":      {"text": f"r/{post['subreddit']} · {post['score']} upvotes · {scored.get('awareness_level','')}"},
        "url":         post["url"],
        "timestamp":   datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if dry_run:
        print(f"    [DRY-RUN] Discord: {hook[:60]}")
        return
    try:
        requests.post(INTEL_WEBHOOK, json={"embeds": [embed]}, timeout=10)
    except Exception as e:
        print(f"  [WARN] Discord post failed: {e}")


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=5, help="Max posts per subreddit to score")
    parser.add_argument("--subreddit", help="Run only this subreddit")
    args = parser.parse_args()

    seen = load_seen()
    subs = [args.subreddit] if args.subreddit else SUBREDDITS
    total_new = 0

    for sub in subs:
        print(f"\n[r/{sub}]")
        posts = fetch_posts(sub)
        new_posts = [p for p in posts if p["url"] not in seen]
        print(f"  {len(posts)} fetched, {len(new_posts)} new")

        scored_count = 0
        for post in new_posts:
            if scored_count >= args.limit:
                break
            seen.add(post["url"])
            scored = score_post(post)
            if not scored:
                print(f"  [skip] {post['title'][:60]}")
                continue
            print(f"  [+] {scored.get('emotion')} | {scored.get('vertical')} | {post['title'][:50]}")
            write_to_baserow(post, scored, args.dry_run)
            fire_discord(post, scored, args.dry_run)
            # Qdrant vectorization (best-effort)
            if _qdrant_upsert and not args.dry_run:
                _qdrant_upsert({
                    **scored,
                    "headline": post["title"],
                    "source_url": post["url"],
                    "source": f"r/{post['subreddit']}",
                    "created_date": datetime.datetime.fromtimestamp(post["created_utc"], datetime.timezone.utc).strftime("%Y-%m-%d"),
                    "signal_type": "reddit",
                })
            scored_count += 1
            total_new += 1
            time.sleep(0.5)

        save_seen(seen)
        print(f"  → {scored_count} relevant posts processed")

    print(f"\n✅ Done — {total_new} audience signals written")


if __name__ == "__main__":
    main()

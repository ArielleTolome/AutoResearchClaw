#!/usr/bin/env python3
"""
qdrant_sink.py — Qdrant vector memory layer for AutoResearchClaw signals.
Embeds and stores signals (news + reddit) for semantic search and trend detection.

Collection: autoresearch-signals (separate from rachel-memories)
Vector dims: 1536 (OpenAI text-embedding-3-small)

Usage (as library):
    from qdrant_sink import upsert_signal, search_similar, get_signals_since

Usage (CLI test):
    python qdrant_sink.py --test
"""

import os, sys, hashlib, datetime, json
from pathlib import Path

import yaml

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance, VectorParams, PointStruct,
        Filter, FieldCondition, Range,
        models,
    )
    HAS_QDRANT = True
except ImportError:
    HAS_QDRANT = False

try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

# ── Config ───────────────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"

def _load_config() -> dict:
    if CFG_PATH.exists():
        return yaml.safe_load(CFG_PATH.read_text()) or {}
    return {}

_CFG = _load_config()
_QDRANT_CFG = _CFG.get("qdrant", {})

QDRANT_URL = _QDRANT_CFG.get("url", "http://37.27.228.106:6333")
QDRANT_API_KEY = _QDRANT_CFG.get("api_key", os.getenv("QDRANT_API_KEY", ""))
QDRANT_ENABLED = _QDRANT_CFG.get("enabled", False)
COLLECTION = _QDRANT_CFG.get("signals_collection", "autoresearch-signals")
VECTOR_DIM = 1536

# OpenAI key: env > qdrant config > llm config
OPENAI_API_KEY = (
    os.getenv("OPENAI_API_KEY")
    or _QDRANT_CFG.get("openai_api_key", "")
    or _CFG.get("llm", {}).get("api_key", "")
)

# ── Qdrant client singleton ─────────────────────────────────────────────────
_client = None

def _get_client() -> "QdrantClient":
    global _client
    if _client is None:
        if not HAS_QDRANT:
            raise ImportError("qdrant-client not installed — pip install qdrant-client")
        kwargs = {"url": QDRANT_URL, "timeout": 15}
        if QDRANT_API_KEY:
            kwargs["api_key"] = QDRANT_API_KEY
        _client = QdrantClient(**kwargs)
    return _client


def _ensure_collection():
    """Create the signals collection if it doesn't exist."""
    client = _get_client()
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION not in collections:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        print(f"[qdrant] Created collection '{COLLECTION}'")


# ── Embedding ────────────────────────────────────────────────────────────────

def _embed(text: str) -> list[float]:
    """Get embedding via OpenAI text-embedding-3-small."""
    if not HAS_OPENAI:
        raise ImportError("openai not installed — pip install openai")
    if not OPENAI_API_KEY:
        raise ValueError("No OpenAI API key configured (set OPENAI_API_KEY env or qdrant.openai_api_key in config)")
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    resp = client.embeddings.create(input=text, model="text-embedding-3-small")
    return resp.data[0].embedding


def _signal_id(signal: dict) -> str:
    """Deterministic point ID from signal content for dedup."""
    key = signal.get("source_url") or (
        (signal.get("headline") or signal.get("title") or "") +
        (signal.get("pub_date") or signal.get("created_date") or "")
    )
    h = hashlib.sha256(key.encode()).hexdigest()
    # Qdrant accepts int or UUID — use first 16 hex chars as int
    return int(h[:16], 16)


# ── Public API ───────────────────────────────────────────────────────────────

def upsert_signal(signal_dict: dict) -> bool:
    """
    Embed and upsert a signal to Qdrant.
    signal_dict should have: vertical, headline/title, summary/pain_point, source_url, pub_date/created_date
    Returns True on success, False on failure.
    """
    if not QDRANT_ENABLED:
        return False

    try:
        _ensure_collection()

        vertical = signal_dict.get("vertical", "")
        headline = signal_dict.get("headline") or signal_dict.get("title", "")
        summary = signal_dict.get("summary") or signal_dict.get("pain_point", "")

        embed_text = f"{vertical} {headline} {summary}".strip()
        if not embed_text:
            return False

        vector = _embed(embed_text)
        point_id = _signal_id(signal_dict)

        payload = {
            "vertical": vertical,
            "headline": headline,
            "summary": summary,
            "source": signal_dict.get("source", ""),
            "source_url": signal_dict.get("source_url") or signal_dict.get("url", ""),
            "emotion": signal_dict.get("emotion", ""),
            "sentiment_impact": signal_dict.get("sentiment_impact", ""),
            "impact_type": signal_dict.get("impact_type", ""),
            "verbatim_hook": signal_dict.get("verbatim_hook", ""),
            "awareness_level": signal_dict.get("awareness_level", ""),
            "signal_type": signal_dict.get("signal_type", "news"),
            "created_at": (
                signal_dict.get("pub_date")
                or signal_dict.get("created_date")
                or datetime.date.today().isoformat()
            ),
        }

        client = _get_client()
        client.upsert(
            collection_name=COLLECTION,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )
        print(f"  [qdrant] Upserted signal: {headline[:60]}")
        return True

    except Exception as e:
        print(f"  [qdrant] Upsert failed: {e}")
        return False


def search_similar(text: str, top_k: int = 5, vertical_filter: str = None) -> list[dict]:
    """Semantic search for similar signals. Returns list of payload dicts with score."""
    if not QDRANT_ENABLED:
        return []

    try:
        _ensure_collection()
        vector = _embed(text)

        query_filter = None
        if vertical_filter:
            query_filter = Filter(
                must=[FieldCondition(key="vertical", match=models.MatchValue(value=vertical_filter))]
            )

        client = _get_client()
        results = client.query_points(
            collection_name=COLLECTION,
            query=vector,
            query_filter=query_filter,
            limit=top_k,
        ).points

        return [
            {**r.payload, "score": r.score}
            for r in results
        ]

    except Exception as e:
        print(f"  [qdrant] Search failed: {e}")
        return []


def get_signals_since(days: int = 7, vertical: str = None) -> list[dict]:
    """Get all signals from the last N days, optionally filtered by vertical."""
    if not QDRANT_ENABLED:
        return []

    try:
        _ensure_collection()
        cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()

        must_conditions = [
            FieldCondition(key="created_at", range=Range(gte=cutoff))
        ]
        if vertical:
            must_conditions.append(
                FieldCondition(key="vertical", match=models.MatchValue(value=vertical))
            )

        client = _get_client()
        results = client.scroll(
            collection_name=COLLECTION,
            scroll_filter=Filter(must=must_conditions),
            limit=500,
        )[0]  # scroll returns (points, next_offset)

        return [r.payload for r in results]

    except Exception as e:
        print(f"  [qdrant] get_signals_since failed: {e}")
        return []


# ── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Qdrant signal memory layer test")
    parser.add_argument("--test", action="store_true", help="Run a test upsert + search")
    args = parser.parse_args()

    if not QDRANT_ENABLED:
        print("[qdrant] Qdrant is disabled in config. Set qdrant.enabled: true")
        sys.exit(0)

    if args.test:
        test_signal = {
            "vertical": "Medicare",
            "headline": "CMS Announces 2027 Medicare Advantage Rate Changes",
            "summary": "CMS released preliminary rates for MA plans, showing 3.5% increase.",
            "source": "CMS.gov",
            "source_url": "https://example.com/test",
            "pub_date": datetime.date.today().isoformat(),
            "signal_type": "news",
            "sentiment_impact": "Bullish",
        }
        print("[test] Upserting test signal...")
        upsert_signal(test_signal)
        print("[test] Searching for 'Medicare rates'...")
        results = search_similar("Medicare rates changes", top_k=3)
        for r in results:
            print(f"  [{r.get('score', 0):.3f}] {r.get('headline', '')[:80]}")
        print("[test] Getting signals from last 7 days...")
        recent = get_signals_since(days=7)
        print(f"  {len(recent)} signals found")
    else:
        print("[qdrant] Use --test to run a test upsert + search")

"""
youtube_source.py — Fetch top-performing YouTube ad/commercial hooks via Data API v3.

Extracts the first 60 seconds of transcript (the "hook") for each video,
falling back to the video description if transcript is unavailable.
"""

import requests


def fetch_youtube_hooks(
    config: dict, topic: str, limit: int = 20, dry_run: bool = False
) -> list[dict]:
    """
    Search YouTube for top ad/commercial videos and extract their hooks.

    Config keys:
      - youtube.api_key: YouTube Data API v3 key
      - youtube.include_transcripts: Whether to attempt transcript fetch (default True)
    """
    if dry_run:
        print("[YOUTUBE] DRY RUN — returning mock data")
        return [
            {
                "video_id": "dQw4w9WgXcQ",
                "title": "Best Auto Insurance Ad 2024",
                "description": "See how we save drivers $500/year on average...",
                "channel_title": "MockChannel",
                "hook_text": "What if I told you that you're overpaying for car insurance every single month? Most people don't realize they could save hundreds.",
            },
            {
                "video_id": "abc123mock",
                "title": "Insurance Commercial That Went Viral",
                "description": "This 30-second spot changed the industry...",
                "channel_title": "AdWorld",
                "hook_text": "Picture this: you're driving home and you get the call. Your rates just went up again. But what if there was another way?",
            },
        ]

    yt_cfg = config.get("youtube", {})
    api_key = yt_cfg.get("api_key", "")
    include_transcripts = yt_cfg.get("include_transcripts", True)

    if not api_key:
        print("[YOUTUBE] No API key configured — skipping")
        return []

    base_url = "https://www.googleapis.com/youtube/v3"

    # Step 1: Search for videos
    search_params = {
        "part": "snippet",
        "q": f"{topic} ad commercial",
        "type": "video",
        "maxResults": limit,
        "key": api_key,
        "order": "viewCount",
    }

    try:
        resp = requests.get(f"{base_url}/search", params=search_params, timeout=30)
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except requests.exceptions.RequestException as e:
        print(f"[YOUTUBE] Search error: {e}")
        return []

    results = []
    for item in items:
        snippet = item.get("snippet", {})
        video_id = item.get("id", {}).get("videoId", "")
        if not video_id:
            continue

        title = snippet.get("title", "")
        description = snippet.get("description", "")
        channel_title = snippet.get("channelTitle", "")

        # Step 2: Try to get transcript (first 60 seconds)
        hook_text = ""
        if include_transcripts:
            hook_text = _get_hook_transcript(video_id)

        # Fall back to first 200 chars of description
        if not hook_text:
            hook_text = description[:200]

        results.append({
            "video_id": video_id,
            "title": title,
            "description": description,
            "channel_title": channel_title,
            "hook_text": hook_text,
        })

    print(f"[YOUTUBE] Fetched {len(results)} video hooks for '{topic}'")
    return results


def _get_hook_transcript(video_id: str) -> str:
    """
    Attempt to get the first 60 seconds of a video's transcript.
    Returns empty string if transcript is unavailable.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        hook_sentences = [
            entry["text"]
            for entry in transcript
            if entry.get("start", 999) < 60
        ]
        return " ".join(hook_sentences)
    except Exception:
        return ""

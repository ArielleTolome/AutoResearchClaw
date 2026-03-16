"""
notion_sink.py — AutoResearchClaw Notion integration

Writes creative outputs from action_handler.py into Notion databases.

Databases (all under AutoResearchClaw hub page):
  📋 Creative Briefs  : 325bbf40-5fd2-81f7-be82-d15e47deef30
  🎣 Hooks Library    : 325bbf40-5fd2-8100-92f1-f92c1b822664
  🗃️ Intel Archive    : 325bbf40-5fd2-818f-9eeb-d2e2b11fa62d
  👤 Personas         : 325bbf40-5fd2-81e2-bb09-c6354d4433bb

Hub page: https://www.notion.so/AutoResearchClaw-325bbf405fd2810d99c1ede1b26541a4
"""

import os
import re
import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import requests
import yaml

log = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────

CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"

def _load_config() -> dict:
    if CFG_PATH.exists():
        with open(CFG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}

CFG = _load_config()

NOTION_TOKEN = (
    CFG.get("notion", {}).get("api_key")
    or os.getenv("NOTION_API_KEY", "")
)

NOTION_VERSION = "2022-06-28"
NOTION_BASE = "https://api.notion.com/v1"

# Database IDs
DB_BRIEFS   = "325bbf40-5fd2-81f7-be82-d15e47deef30"
DB_HOOKS    = "325bbf40-5fd2-8100-92f1-f92c1b822664"
DB_INTEL    = "325bbf40-5fd2-818f-9eeb-d2e2b11fa62d"
DB_PERSONAS = "325bbf40-5fd2-81e2-bb09-c6354d4433bb"

HUB_URL = "https://www.notion.so/AutoResearchClaw-325bbf405fd2810d99c1ede1b26541a4"

# ── Helpers ──────────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

def _rich_text(text: str) -> list:
    """Split text into 2000-char chunks (Notion rich_text limit per block)."""
    chunks = [text[i:i+2000] for i in range(0, len(text), 2000)]
    return [{"type": "text", "text": {"content": c}} for c in chunks]

def _paragraph_blocks(text: str) -> list:
    """Convert plain text into paragraph blocks, splitting on double newlines."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    blocks = []
    for para in paragraphs:
        # Split into heading vs body
        if para.startswith("# "):
            blocks.append({
                "object": "block",
                "type": "heading_1",
                "heading_1": {"rich_text": _rich_text(para[2:].strip())}
            })
        elif para.startswith("## "):
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": _rich_text(para[3:].strip())}
            })
        elif para.startswith("### "):
            blocks.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": _rich_text(para[4:].strip())}
            })
        else:
            # Chunk long paragraphs
            for chunk in [para[i:i+2000] for i in range(0, len(para), 2000)]:
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]}
                })
    return blocks[:100]  # Notion max 100 blocks per append


def _create_page(database_id: str, properties: dict, content: str = "") -> Optional[dict]:
    """Create a page in a Notion database."""
    if not NOTION_TOKEN:
        log.warning("[Notion] No API key configured — skipping write")
        return None

    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }

    # Add first 100 blocks as page body
    if content:
        payload["children"] = _paragraph_blocks(content)

    resp = requests.post(f"{NOTION_BASE}/pages", headers=_headers(), json=payload, timeout=15)

    if resp.status_code == 200:
        page = resp.json()
        log.info(f"[Notion] Created page: {page.get('url')}")
        return page
    else:
        log.error(f"[Notion] Failed to create page: {resp.status_code} {resp.text[:200]}")
        return None


def _append_blocks(page_id: str, content: str):
    """Append additional blocks to a page (for long content > 100 blocks)."""
    blocks = _paragraph_blocks(content)
    for i in range(0, len(blocks), 100):
        batch = blocks[i:i+100]
        resp = requests.patch(
            f"{NOTION_BASE}/blocks/{page_id}/children",
            headers=_headers(),
            json={"children": batch},
            timeout=15
        )
        if resp.status_code != 200:
            log.error(f"[Notion] Failed to append blocks: {resp.status_code} {resp.text[:200]}")


# ── Public API ───────────────────────────────────────────────────────────────

def write_brief(signal: dict, brief_content: str, platform: str = "Meta") -> Optional[str]:
    """
    Write a creative brief to the Creative Briefs database.
    Returns the Notion page URL or None on failure.
    """
    headline = signal.get("headline", "Untitled Brief")
    vertical = signal.get("vertical", "Other")
    signal_id = signal.get("signal_id", "")

    # Normalize vertical to known options
    vertical_map = {
        "insurance": "Insurance", "finance": "Finance", "health": "Health",
        "legal": "Legal", "ecomm": "eComm", "ecommerce": "eComm",
    }
    vertical_clean = vertical_map.get(vertical.lower(), "Other")
    platform_map = {"meta": "Meta", "native": "Native", "tiktok": "TikTok", "youtube": "YouTube"}
    platform_clean = platform_map.get(platform.lower(), "Meta")

    properties = {
        "Name": {"title": _rich_text(headline[:200])},
        "Signal": {"rich_text": _rich_text(signal.get("summary", "")[:2000])},
        "Vertical": {"select": {"name": vertical_clean}},
        "Platform": {"select": {"name": platform_clean}},
        "Status": {"select": {"name": "Draft"}},
        "Created": {"date": {"start": date.today().isoformat()}},
        "Source Signal ID": {"rich_text": _rich_text(signal_id[:200])},
    }

    page = _create_page(DB_BRIEFS, properties, brief_content)
    if page:
        url = page.get("url", "")
        # Append remaining content if brief is long
        if len(_paragraph_blocks(brief_content)) > 100:
            remaining = "\n\n".join(brief_content.split("\n\n")[100:])
            _append_blocks(page["id"], remaining)
        return url
    return None


def write_hooks(signal: dict, hooks_content: str, platform: str = "Meta") -> Optional[str]:
    """
    Write hooks to the Hooks Library database.
    Parses numbered hooks from content, writes each as a row.
    Returns URL of a summary page or None.
    """
    headline = signal.get("headline", "Untitled")
    vertical = signal.get("vertical", "Other")

    vertical_map = {
        "insurance": "Insurance", "finance": "Finance", "health": "Health",
        "legal": "Legal", "ecomm": "eComm", "ecommerce": "eComm",
    }
    vertical_clean = vertical_map.get(vertical.lower(), "Other")
    platform_map = {"meta": "Meta", "native": "Native", "tiktok": "TikTok", "youtube": "YouTube"}
    platform_clean = platform_map.get(platform.lower(), "Meta")

    # Parse individual hooks from the content (lines starting with 1., 2., etc.)
    hook_lines = re.findall(r'^\d+[\.\)]\s+(.+)', hooks_content, re.MULTILINE)

    if not hook_lines:
        # Fallback: treat entire content as one entry
        hook_lines = [hooks_content[:500]]

    created_urls = []
    for hook_text in hook_lines[:20]:  # cap at 20
        hook_text = hook_text.strip()
        if not hook_text:
            continue

        # Detect hook type from keywords
        hook_type = "Direct"
        lower = hook_text.lower()
        if "?" in hook_text:
            hook_type = "Question"
        elif any(w in lower for w in ["never", "stop", "worst", "mistake", "warning", "danger"]):
            hook_type = "Negative"
        elif any(w in lower for w in ["%", "out of", "in 10", "million", "billion"]):
            hook_type = "Statistic"
        elif any(w in lower for w in ["story", "when i", "one day", "i was"]):
            hook_type = "Story"
        elif any(w in lower for w in ["secret", "what they don't", "nobody tells"]):
            hook_type = "Curiosity"
        elif any(w in lower for w in ["limited", "expires", "today only", "last chance"]):
            hook_type = "Urgency"
        elif any(w in lower for w in ["most people", "everyone thinks", "contrary"]):
            hook_type = "Contrarian"

        properties = {
            "Hook": {"title": _rich_text(hook_text[:200])},
            "Type": {"select": {"name": hook_type}},
            "Vertical": {"select": {"name": vertical_clean}},
            "Platform": {"select": {"name": platform_clean}},
            "Signal": {"rich_text": _rich_text(headline[:2000])},
            "Created": {"date": {"start": date.today().isoformat()}},
        }

        page = _create_page(DB_HOOKS, properties)
        if page:
            created_urls.append(page.get("url", ""))

    log.info(f"[Notion] Wrote {len(created_urls)} hooks for signal: {headline}")
    return created_urls[0] if created_urls else None


def write_intel(signal: dict) -> Optional[str]:
    """
    Write a raw signal/intel item to the Intel Archive database.
    """
    headline = signal.get("headline", "Untitled Signal")
    vertical = signal.get("vertical", "Other")
    source = signal.get("source", "News")
    emotion = signal.get("emotion", "Frustrated")
    summary = signal.get("summary", "")
    url = signal.get("url", "")
    score = signal.get("signal_score") or signal.get("score")

    vertical_map = {
        "insurance": "Insurance", "finance": "Finance", "health": "Health",
        "legal": "Legal", "ecomm": "eComm", "ecommerce": "eComm",
    }
    vertical_clean = vertical_map.get(vertical.lower(), "Other")

    source_map = {
        "reddit": "Reddit", "news": "News", "competitor": "Competitor Ad",
        "facebook": "Facebook Library", "fb": "Facebook Library",
        "anstrex": "Anstrex", "youtube": "YouTube Comments",
        "amazon": "Amazon Reviews",
    }
    source_clean = source_map.get(str(source).lower(), "News")

    emotion_map = {
        "frustrated": "Frustrated", "hopeful": "Hopeful", "confused": "Confused",
        "angry": "Angry", "relieved": "Relieved",
    }
    emotion_clean = emotion_map.get(str(emotion).lower(), "Frustrated")

    properties = {
        "Headline": {"title": _rich_text(headline[:200])},
        "Source": {"select": {"name": source_clean}},
        "Vertical": {"select": {"name": vertical_clean}},
        "Emotion": {"select": {"name": emotion_clean}},
        "Summary": {"rich_text": _rich_text(summary[:2000])},
        "Created": {"date": {"start": date.today().isoformat()}},
    }
    if url:
        properties["URL"] = {"url": url}
    if score is not None:
        try:
            properties["Signal Score"] = {"number": float(score)}
        except (TypeError, ValueError):
            pass

    page = _create_page(DB_INTEL, properties)
    return page.get("url") if page else None


def write_persona(signal: dict, persona_content: str) -> Optional[str]:
    """
    Write a persona card to the Personas database.
    """
    headline = signal.get("headline", "Untitled Persona")
    vertical = signal.get("vertical", "Other")

    vertical_map = {
        "insurance": "Insurance", "finance": "Finance", "health": "Health",
        "legal": "Legal", "ecomm": "eComm", "ecommerce": "eComm",
    }
    vertical_clean = vertical_map.get(vertical.lower(), "Other")

    # Try to extract key fields from persona content
    pain_match = re.search(r'(?:primary pain|main pain|core pain)[:\s]+(.+)', persona_content, re.IGNORECASE)
    trigger_match = re.search(r'(?:trigger event|trigger)[:\s]+(.+)', persona_content, re.IGNORECASE)
    verbatim_match = re.search(r'(?:key verbatim|verbatim|they say)[:\s]+(.+)', persona_content, re.IGNORECASE)
    awareness_match = re.search(r'(?:awareness stage|awareness)[:\s]+(.+)', persona_content, re.IGNORECASE)

    awareness_options = ["Unaware", "Problem Aware", "Solution Aware", "Product Aware", "Most Aware"]
    awareness = "Problem Aware"
    if awareness_match:
        raw = awareness_match.group(1).strip()
        for opt in awareness_options:
            if opt.lower() in raw.lower():
                awareness = opt
                break

    properties = {
        "Name": {"title": _rich_text(headline[:200])},
        "Vertical": {"select": {"name": vertical_clean}},
        "Awareness Stage": {"select": {"name": awareness}},
        "Created": {"date": {"start": date.today().isoformat()}},
    }
    if pain_match:
        properties["Primary Pain"] = {"rich_text": _rich_text(pain_match.group(1).strip()[:2000])}
    if trigger_match:
        properties["Trigger Event"] = {"rich_text": _rich_text(trigger_match.group(1).strip()[:2000])}
    if verbatim_match:
        properties["Key Verbatim"] = {"rich_text": _rich_text(verbatim_match.group(1).strip()[:2000])}

    page = _create_page(DB_PERSONAS, properties, persona_content)
    return page.get("url") if page else None


# ── Action router ────────────────────────────────────────────────────────────

def write_action_output(action: str, signal: dict, content: str, platform: str = "Meta") -> Optional[str]:
    """
    Route action output to the correct Notion database.
    Returns the Notion page URL or None.
    """
    action_map = {
        "generate_brief": write_brief,
        "build_persona":  write_persona,
        "generate_hooks": write_hooks,
        "write_script":   write_brief,   # scripts go to Briefs db
        "image_concept":  write_brief,   # image concepts go to Briefs db
    }

    fn = action_map.get(action)
    if not fn:
        log.warning(f"[Notion] Unknown action: {action}")
        return None

    if action in ("generate_brief", "write_script", "image_concept"):
        return fn(signal, content, platform)
    elif action == "build_persona":
        return fn(signal, content)
    elif action == "generate_hooks":
        return fn(signal, content, platform)

    return None


if __name__ == "__main__":
    # Quick smoke test
    test_signal = {
        "headline": "People frustrated with high insurance premiums",
        "vertical": "Insurance",
        "summary": "Reddit users are venting about insurance costs doubling.",
        "signal_id": "test-abc123",
        "emotion": "Frustrated",
        "source": "Reddit",
        "url": "https://reddit.com/r/Insurance",
    }
    print("Testing Notion sink...")
    url = write_intel(test_signal)
    print(f"Intel page: {url}")
    url = write_brief(test_signal, "## Brief\n\nTest brief content here.\n\n### Hooks\n\n1. Are your insurance premiums eating your paycheck?\n\n### Body\n\nBody copy here.", "Meta")
    print(f"Brief page: {url}")
    url = write_hooks(test_signal, "1. Are you overpaying for insurance?\n2. Insurance companies don't want you to know this\n3. What if you could cut your premium in half?", "Meta")
    print(f"Hooks: {url}")
    url = write_persona(test_signal, "Primary Pain: Rising premiums with no explanation\nTrigger Event: Renewal notice arrived\nKey Verbatim: 'Why did my rate go up 40%?'\nAwareness Stage: Problem Aware")
    print(f"Persona page: {url}")

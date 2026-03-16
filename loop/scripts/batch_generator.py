"""
batch_generator.py — Generate a full batch of ad variations from a concept.

Usage:
  python batch_generator.py --concept-id c001 --batch-size 15
  python batch_generator.py --topic "auto insurance rate hike" --platform meta --batch-size 15
  python batch_generator.py --all-proven --batch-size 15
"""

import json
import yaml
import argparse
import datetime
import requests
from pathlib import Path

import anthropic

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
RUNS_DIR = ROOT / "learnings" / "runs"
OUTPUT_DIR = ROOT / "output"

# Ensure output dir exists
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
RUNS_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def load_creative_context() -> str:
    """Try loading HOOKS.md and ANGLES.md from {ROOT}/../creative/ if they exist."""
    creative_path = ROOT.parent / "creative"
    chunks = []
    for fname in ["HOOKS.md", "ANGLES.md"]:
        fpath = creative_path / fname
        if fpath.exists():
            content = fpath.read_text()[:2000]
            chunks.append(f"=== {fname} ===\n{content}")
    return "\n\n".join(chunks)


def post_to_discord(webhook_url: str, content: str, title: str) -> None:
    """Post batch result to Discord webhook."""
    if not webhook_url:
        return
    try:
        # Truncate to Discord embed description limit
        preview = content[:3800] if len(content) > 3800 else content
        payload = {
            "embeds": [
                {
                    "title": f"⚙️ Batch Generated: {title}",
                    "description": f"```\n{preview[:3800]}\n```",
                    "color": 0x9B59B6,
                    "footer": {"text": "AutoResearchClaw v2.3 · The Batch Machine"},
                }
            ]
        }
        resp = requests.post(webhook_url, json=payload, timeout=15)
        if resp.status_code not in (200, 204):
            print(f"[BATCH] Discord webhook returned {resp.status_code}")
    except Exception as e:
        print(f"[BATCH] Discord webhook error: {e}")


def build_batch_prompt(concept: dict, batch_size: int, creative_context: str) -> str:
    """Build the LLM prompt for batch generation."""
    name = concept.get("name", "Unknown Concept")
    description = concept.get("description", "")
    platform = concept.get("platform", "meta")
    vertical = concept.get("vertical", "")

    hook_types_instruction = (
        "Use a VARIETY of these 17 ACA hook types (pick 5 distinct ones):\n"
        "1. Question  2. Shocking Stat  3. Contrarian  4. Story  5. How-To\n"
        "6. Fear  7. Desire  8. Social Proof  9. Newsjacking  10. Empathy\n"
        "11. Teaser  12. Negative  13. Bold Claim  14. Before/After  "
        "15. Authority  16. Urgency  17. Identification\n"
    )

    creative_section = ""
    if creative_context:
        creative_section = f"\n--- CREATIVE FRAMEWORKS ---\n{creative_context}\n\n"

    return f"""You are a direct-response ad creative strategist. Generate a complete ad variation batch.

CONCEPT: {name}
DESCRIPTION: {description}
VERTICAL: {vertical}
PLATFORM: {platform}
TARGET BATCH SIZE: {batch_size} unique ads

{creative_section}
{hook_types_instruction}

Produce the following:

## HOOKS (5 total — use 5 distinct hook types from the 17 above)
For each hook, specify:
- Hook Type (#N Name)
- Hook text (0-3 seconds, written out)
- Why it works for this concept

## ACTOR/VOICE PERSONAS (3 total)
Describe each as casting notes, e.g.:
- Actor A: Female, 30s, relatable in-car UGC, frustrated-to-relieved tone
- Actor B: Male, 45-55, authoritative, straight-to-camera news-desk style
- Actor C: Female, 25, energetic text-overlay style, no presenter

## MUSIC SCENARIOS (2 total)
- Scenario 1: No music — silent or ambient only (reason)
- Scenario 2: Upbeat background — describe BPM/mood

## SCENE VARIATIONS (2 total)
- Scene A: Setting, visual style, B-roll notes
- Scene B: Alternative setting with different emotional tone

## MULTIPLICATION TABLE
Show the full matrix:
5 hooks × 3 actors × 2 music × 2 scenes = {5 * 3 * 2 * 2} unique ads

List each unique ad combination as a numbered row:
Ad #1: Hook 1 + Actor A + No Music + Scene A
Ad #2: Hook 1 + Actor A + No Music + Scene B
... (continue for all combinations)

## PRODUCTION NOTES
Key guidance for the production team. Platform-specific specs for {platform}.
"""


def generate_batch(concept: dict, batch_size: int, config: dict) -> str:
    """Call LLM to generate the batch. Returns markdown string."""
    creative_context = load_creative_context()
    prompt = build_batch_prompt(concept, batch_size, creative_context)

    client_kwargs = {"api_key": config["llm"]["api_key"]}
    if config["llm"].get("base_url"):
        client_kwargs["base_url"] = config["llm"]["base_url"]
    client = anthropic.Anthropic(**client_kwargs)

    print(f"[BATCH] Calling LLM ({config['llm']['model']}) for batch generation…")
    message = client.messages.create(
        model=config["llm"]["model"],
        max_tokens=4096,
        system=(
            "You are Rachel, an expert ad creative strategist trained in ACA (Ad Creative Academy) methodology. "
            "You produce precise, production-ready ad variation matrices. "
            "Be specific, tactical, and structured. Every hook must be written out fully."
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def run_batch(concept: dict, batch_size: int, config: dict, label: str) -> None:
    """Generate batch, save files, post to Discord."""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"[BATCH] Generating {batch_size}-ad batch for: {label}")
    result = generate_batch(concept, batch_size, config)

    # Save markdown
    md_filename = f"{ts}_batch_{label.replace(' ', '_')[:40]}.md"
    md_path = RUNS_DIR / md_filename
    md_path.write_text(result)
    print(f"[BATCH] Saved markdown → {md_path}")

    # Save JSON manifest
    manifest = {
        "ts": ts,
        "label": label,
        "concept_id": concept.get("concept_id"),
        "concept_name": concept.get("name"),
        "platform": concept.get("platform"),
        "batch_size": batch_size,
        "md_path": str(md_path),
        "result_preview": result[:500],
    }
    json_path = OUTPUT_DIR / f"batch_{ts}.json"
    json_path.write_text(json.dumps(manifest, indent=2))
    print(f"[BATCH] Saved manifest → {json_path}")

    # Post to Discord
    discord_webhook = config.get("notifications", {}).get("discord_webhook", "")
    post_to_discord(discord_webhook, result, label)

    # Print summary
    print(f"\n{'='*60}\nBATCH COMPLETE: {label}\n{'='*60}")
    print(result[:1000])
    print(f"\n{'='*60}")
    print(f"Full output: {md_path}")


def main():
    parser = argparse.ArgumentParser(description="Batch Generator — produce full ad variation batches")
    parser.add_argument("--concept-id", help="Concept vault ID (e.g. c001)")
    parser.add_argument("--topic", help="Ad-hoc topic (creates temporary concept)")
    parser.add_argument("--platform", default="meta", help="Ad platform (default: meta)")
    parser.add_argument("--batch-size", type=int, default=15, help="Target number of unique ads")
    parser.add_argument("--all-proven", action="store_true", help="Run batches for all proven concepts")
    args = parser.parse_args()

    config = load_config()

    # Import concept_vault for vault operations
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from concept_vault import load_vault, get_proven, update_concept
    except ImportError as e:
        print(f"[BATCH] Warning: could not import concept_vault: {e}")
        load_vault = lambda: []
        get_proven = lambda: []
        update_concept = lambda *a, **kw: None

    if args.all_proven:
        proven = get_proven()
        if not proven:
            print("[BATCH] No proven concepts in vault. Run some batches first.")
            return
        for concept in proven:
            run_batch(concept, args.batch_size, config, concept["concept_id"])
            update_concept(
                concept["concept_id"],
                batch_count=concept.get("batch_count", 0) + 1,
                last_iterated=str(datetime.date.today()),
            )
        return

    if args.concept_id:
        vault = load_vault()
        concept = next((c for c in vault if c["concept_id"] == args.concept_id), None)
        if not concept:
            print(f"[BATCH] Concept {args.concept_id} not found in vault.")
            return
        run_batch(concept, args.batch_size, config, args.concept_id)
        update_concept(
            args.concept_id,
            batch_count=concept.get("batch_count", 0) + 1,
            last_iterated=str(datetime.date.today()),
        )
        return

    if args.topic:
        # Ad-hoc concept (not saved to vault)
        concept = {
            "concept_id": None,
            "name": args.topic,
            "description": args.topic,
            "vertical": config.get("offer", {}).get("niche", "general"),
            "platform": args.platform,
            "status": "testing",
            "batch_count": 0,
            "hook_rate_best": None,
            "ctr_best": None,
            "cpa_best": None,
            "winning_hook_types": [],
            "created_at": str(datetime.date.today()),
            "last_iterated": None,
            "notes": "Ad-hoc batch — not tracked in vault",
        }
        safe_label = args.topic.replace(" ", "_")[:40]
        run_batch(concept, args.batch_size, config, safe_label)
        return

    parser.print_help()
    print("\nError: provide --concept-id, --topic, or --all-proven")


if __name__ == "__main__":
    main()

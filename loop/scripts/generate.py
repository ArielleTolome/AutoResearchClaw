"""
generate.py — Write a new challenger creative using:
  - The current winner (new baseline)
  - The accumulated learnings log
  - Creative framework files (HOOKS.md, ANGLES.md, etc.)
  - The system prompt from prompts.yaml

Output: A structured challenger brief saved to learnings/runs/{ts}_challenger.md
"""

import yaml
import random
import datetime
from pathlib import Path
from datetime import datetime as _datetime

from agent_runner import run_prompt as _agent_run_prompt

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
PROMPTS_PATH = ROOT / "config" / "prompts.yaml"
BASELINE_PATH = ROOT / "config" / "baseline.md"
LEARNINGS_PATH = ROOT / "learnings" / "learnings.md"
RUNS_DIR = ROOT / "learnings" / "runs"

# Framework files to load as context
FRAMEWORK_FILES = [
    "HOOKS.md",
    "ANGLES.md",
    "ANALYSIS_WORKFLOW.md",
    "AWARENESS.md",
    "PSYCHOLOGY.md",
]


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def load_prompts():
    with open(PROMPTS_PATH) as f:
        return yaml.safe_load(f)


def load_frameworks(config: dict) -> str:
    """Load creative framework files as context."""
    frameworks_path = Path(config.get("frameworks_path", "../creative"))
    if not frameworks_path.is_absolute():
        frameworks_path = ROOT / frameworks_path

    chunks = []
    for fname in FRAMEWORK_FILES:
        fpath = frameworks_path / fname
        if fpath.exists():
            content = fpath.read_text()[:3000]  # cap per file to avoid token blowout
            chunks.append(f"=== {fname} ===\n{content}")
        else:
            print(f"  [WARN] Framework file not found: {fpath}")

    return "\n\n".join(chunks)


def load_learnings() -> str:
    if LEARNINGS_PATH.exists():
        return LEARNINGS_PATH.read_text()
    return "No learnings yet. This is the first cycle."


def load_baseline(winner: dict | None = None) -> str:
    """
    If we have a winner from this cycle, use its ad name as context.
    Otherwise fall back to the static baseline.md.
    """
    baseline = BASELINE_PATH.read_text()
    if winner:
        baseline += f"\n\n## Current Winner\nAd: {winner['ad_name']}\n"
        baseline += f"Hook Rate: {winner.get('hook_rate', 0):.0%} | CTR: {winner.get('ctr', 0):.1%} | CVR: {winner.get('cvr', 0):.1%} | CPA: ${winner.get('cpa') or '—'}"
    return baseline


def recall_from_qdrant(offer_context: str, config: dict) -> str:
    """Pull top relevant memories from Qdrant to seed the generator."""
    from openai import OpenAI
    from qdrant_client import QdrantClient

    qdrant_cfg = config.get("qdrant", {})
    qdrant_url = qdrant_cfg.get("url", "http://37.27.228.106:6333")
    collection = qdrant_cfg.get("collection", "rachel-memories")
    openai_key = qdrant_cfg.get("openai_api_key", "")
    top_k = qdrant_cfg.get("recall_top_k", 5)

    if not openai_key:
        return "Qdrant enabled but no OpenAI API key configured."

    client = OpenAI(api_key=openai_key)
    embedding_resp = client.embeddings.create(
        model="text-embedding-3-small",
        input=offer_context,
    )
    vector = embedding_resp.data[0].embedding

    qdrant_client = QdrantClient(url=qdrant_url)
    results = qdrant_client.search(
        collection_name=collection,
        query_vector=vector,
        limit=top_k,
        with_payload=True,
    )

    if not results:
        return "No prior memories found for this offer/platform."

    chunks = [f"- [{r.payload.get('cycle', '?')}] {r.payload.get('text', '')}" for r in results]
    return "\n".join(chunks)


def generate_challenger(winner: dict | None, dry_run: bool = False, fatigue_context: str = "") -> str:
    config = load_config()
    prompts = load_prompts()

    print("[GENERATE] Loading frameworks and learnings...")
    frameworks = load_frameworks(config)
    learnings = load_learnings()
    baseline = load_baseline(winner)
    qdrant_memories = ""

    offer = config["offer"]
    if config.get("qdrant", {}).get("enabled", False) and not dry_run:
        offer_ctx = f"{offer['name']} {offer.get('niche', '')} {offer.get('platform', 'meta')}"
        print("[GENERATE] Recalling Qdrant memories...")
        qdrant_memories = recall_from_qdrant(offer_ctx, config)
        print(f"  Found {qdrant_memories.count(chr(10)) + 1} relevant memories")

    qdrant_block = ""
    if qdrant_memories:
        qdrant_block = f"--- QDRANT MEMORY RECALL (cross-campaign patterns) ---\n{qdrant_memories}\n\n"

    fatigue_block = ""
    if fatigue_context:
        fatigue_block = f"--- ANGLE FATIGUE (avoid DEAD, prefer FRESH/WARMING) ---\n{fatigue_context}\n\n"

    # ── 80/20 Iteration Rule ──────────────────────────────────────────────────
    proven = []
    try:
        import sys as _sys
        import importlib as _importlib
        _scripts_dir = str(Path(__file__).parent)
        if _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        _cv = _importlib.import_module("concept_vault")
        proven = _cv.get_proven()
    except Exception:
        proven = []

    iteration_ratio = config.get("iteration_ratio", 0.8)
    use_iteration = bool(proven) and random.random() < iteration_ratio
    iteration_block = ""
    chosen_concept = None

    if use_iteration:
        chosen_concept = random.choice(proven)
        iteration_block = (
            f"\n\n--- PROVEN CONCEPT TO ITERATE ---\n"
            f"Concept: {chosen_concept['name']}\n"
            f"Description: {chosen_concept['description']}\n"
            f"Best Hook Rate: {chosen_concept.get('hook_rate_best') or 'unknown'}\n"
            f"Winning Hook Types: {', '.join(chosen_concept.get('winning_hook_types', [])) or 'none yet'}\n\n"
            "Make ONE meaningful change to this proven concept: try a different hook type, "
            "different actor persona, or different scene. Keep the core concept intact.\n"
        )
        print(f"[GENERATE] 80/20 mode: iterating on proven concept '{chosen_concept['name']}'")
    else:
        iteration_block = "\n\n--- MODE: FRESH CONCEPT ---\nCreate a completely new angle not yet in the learnings.\n"
        print("[GENERATE] 80/20 mode: generating fresh concept (20%)")

    user_prompt = (
        f"OFFER: {offer['name']}\n"
        f"NICHE: {offer['niche']}\n"
        f"AWARENESS STAGE: {offer['awareness_stage']} (Schwartz scale)\n"
        f"TARGET CPA: ${offer['target_cpa']}\n\n"
        f"--- CURRENT BASELINE ---\n{baseline}\n\n"
        f"--- ACCUMULATED LEARNINGS ---\n{learnings}\n\n"
        f"{qdrant_block}"
        f"{fatigue_block}"
        f"--- CREATIVE FRAMEWORKS ---\n{frameworks}\n\n"
        f"{iteration_block}"
        "Write the next challenger creative. Make ONE meaningful change. "
        "Ground it in the learnings. Don't repeat what's already failed. "
        "Avoid angles marked DEAD in the fatigue data — prioritize FRESH and WARMING angles."
    )

    if dry_run:
        print("[GENERATE] DRY RUN — skipping LLM call")
        challenger = _mock_challenger()
    else:
        print("[GENERATE] Calling LLM for challenger copy...")
        challenger = _agent_run_prompt(
            prompts["generate"],
            user_prompt,
            max_tokens=2048,
            config=config,
        )

    print(f"\n{'='*60}\nCHALLENGER GENERATED:\n{'='*60}\n{challenger}\n{'='*60}\n")

    # ── Update concept vault after generation ─────────────────────────────────
    if use_iteration and chosen_concept:
        try:
            import sys as _sys2
            _scripts_dir2 = str(Path(__file__).parent)
            if _scripts_dir2 not in _sys2.path:
                _sys2.path.insert(0, _scripts_dir2)
            from concept_vault import update_concept as _update_concept
            _update_concept(
                chosen_concept["concept_id"],
                last_iterated=str(datetime.date.today()),
                batch_count=chosen_concept.get("batch_count", 0) + 1,
            )
            print(f"[GENERATE] Updated vault for concept {chosen_concept['concept_id']}")
        except Exception as _e:
            print(f"[GENERATE] Could not update concept vault: {_e}")

    # Save
    ts = _datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RUNS_DIR / f"{ts}_challenger.md"
    out_path.write_text(challenger)
    print(f"[GENERATE] Saved to {out_path}")

    return challenger


def _mock_challenger() -> str:
    return """CHALLENGER NAME: hook-question-rate-hike-v1
HYPOTHESIS: The baseline hook is a statement. Testing a question hook (type #9) targeting the "rate hike outrage" angle to see if Stage 3 awareness performs better than the current Stage 2 play.
ANGLE: Rate Hike Outrage (Angle #4 — Problem-Aware Agitation)
HOOK TYPE: Question Hook (#9)
AWARENESS STAGE: 3

--- HOOK ---
Did your car insurance just quietly go up — without you filing a single claim?

--- BODY ---
Most people don't notice until renewal. Insurance companies raise rates industry-wide, and loyal customers get hit hardest.
Takes 2 minutes to compare. Most drivers find better coverage for less.

--- CTA ---
See what your neighbors are paying →

--- FORMAT ---
Type: static (image: frustrated person opening insurance bill)
Length: N/A
Persona: N/A (no presenter — copy-forward)

--- STRATEGIC RATIONALE ---
Previous baseline used a loyalty-tax statement hook (Stage 2). Testing question hook at Stage 3 (solution-aware) because learnings show our audience already knows they can switch — they just haven't felt enough urgency. Question creates cognitive dissonance. "Rate hike outrage" angle hasn't been tested yet. CTA uses social proof framing ("neighbors") which PSYCHOLOGY.md confirms activates bandwagon principle."""


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("--winner-file", help="Path to analysis JSON with winner")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    winner = None
    if args.winner_file:
        data = json.loads(Path(args.winner_file).read_text())
        winner = data.get("winner")

    generate_challenger(winner, dry_run=args.dry_run)

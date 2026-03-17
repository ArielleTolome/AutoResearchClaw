from __future__ import annotations
"""
concept_vault.py — Tracks ad concepts across their lifecycle.

Local JSON store at loop/learnings/concept_vault.json.

CLI:
  python concept_vault.py list
  python concept_vault.py add --name X --desc Y --vertical Z --platform P
  python concept_vault.py promote --id c001 --hook-rate 0.35 --ctr 0.012 --cpa 4.20
  python concept_vault.py kill --id c001
"""

import json
import datetime
import argparse
from pathlib import Path

ROOT = Path(__file__).parent.parent
VAULT_PATH = ROOT / "learnings" / "concept_vault.json"


# ── Persistence ───────────────────────────────────────────────────────────────

def load_vault() -> list:
    """Load all concept dicts from local JSON. Returns empty list if file missing."""
    if not VAULT_PATH.exists():
        return []
    try:
        return json.loads(VAULT_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def save_vault(concepts: list) -> None:
    """Write concept list to local JSON."""
    VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    VAULT_PATH.write_text(json.dumps(concepts, indent=2))


# ── CRUD ──────────────────────────────────────────────────────────────────────

def add_concept(name: str, description: str, vertical: str, platform: str, notes: str = "") -> dict:
    """Add a new concept, save, and return it."""
    concepts = load_vault()

    # Generate next concept_id
    existing_ids = [c["concept_id"] for c in concepts if c["concept_id"].startswith("c")]
    max_num = 0
    for cid in existing_ids:
        try:
            max_num = max(max_num, int(cid[1:]))
        except ValueError:
            pass
    new_id = f"c{max_num + 1:03d}"

    concept = {
        "concept_id": new_id,
        "name": name,
        "description": description,
        "vertical": vertical,
        "platform": platform,
        "status": "testing",
        "batch_count": 0,
        "hook_rate_best": None,
        "ctr_best": None,
        "cpa_best": None,
        "winning_hook_types": [],
        "created_at": str(datetime.date.today()),
        "last_iterated": None,
        "notes": notes,
    }

    concepts.append(concept)
    save_vault(concepts)
    return concept


def update_concept(concept_id: str, **kwargs) -> dict | None:
    """Update fields on a concept by ID, save, and return updated concept."""
    concepts = load_vault()
    for concept in concepts:
        if concept["concept_id"] == concept_id:
            for key, value in kwargs.items():
                concept[key] = value
            save_vault(concepts)
            return concept
    return None


# ── Queries ───────────────────────────────────────────────────────────────────

def get_proven() -> list:
    """Return concepts with status 'proven' or 'scaling'."""
    return [c for c in load_vault() if c["status"] in ("proven", "scaling")]


def get_testing() -> list:
    """Return concepts with status 'testing'."""
    return [c for c in load_vault() if c["status"] == "testing"]


def get_all() -> list:
    """Return all non-dead concepts sorted by batch_count ascending."""
    return sorted(
        [c for c in load_vault() if c["status"] != "dead"],
        key=lambda c: c.get("batch_count", 0),
    )


# ── Lifecycle Actions ─────────────────────────────────────────────────────────

def promote(concept_id: str, hook_rate: float, ctr: float, cpa: float) -> dict | None:
    """
    Promote concept to 'proven' if hook_rate > 0.30.
    Always updates metrics.
    """
    concepts = load_vault()
    for concept in concepts:
        if concept["concept_id"] == concept_id:
            concept["hook_rate_best"] = hook_rate
            concept["ctr_best"] = ctr
            concept["cpa_best"] = cpa
            if hook_rate > 0.30:
                concept["status"] = "proven"
                print(f"[VAULT] Promoted {concept_id} to PROVEN (hook rate {hook_rate:.0%})")
            else:
                print(f"[VAULT] Metrics updated for {concept_id} — hook rate {hook_rate:.0%} below 30% threshold, staying in testing")
            save_vault(concepts)
            return concept
    print(f"[VAULT] Concept {concept_id} not found")
    return None


def kill(concept_id: str) -> dict | None:
    """Set concept status to 'dead'."""
    result = update_concept(concept_id, status="dead")
    if result:
        print(f"[VAULT] Killed concept {concept_id}: {result['name']}")
    else:
        print(f"[VAULT] Concept {concept_id} not found")
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_concept(c: dict) -> None:
    hr = f"{c['hook_rate_best']:.0%}" if c.get("hook_rate_best") is not None else "—"
    ctr = f"{c['ctr_best']:.1%}" if c.get("ctr_best") is not None else "—"
    cpa = f"${c['cpa_best']:.2f}" if c.get("cpa_best") is not None else "—"
    hooks = ", ".join(c.get("winning_hook_types", [])) or "none"
    print(
        f"  [{c['concept_id']}] {c['name'][:60]}\n"
        f"    Status: {c['status']} | Batches: {c['batch_count']} | "
        f"Hook Rate: {hr} | CTR: {ctr} | CPA: {cpa}\n"
        f"    Vertical: {c['vertical']} | Platform: {c['platform']}\n"
        f"    Winning Hooks: {hooks}\n"
        f"    Notes: {c.get('notes', '')[:80]}\n"
    )


def main():
    parser = argparse.ArgumentParser(description="Concept Vault CLI")
    subparsers = parser.add_subparsers(dest="command")

    # list
    subparsers.add_parser("list", help="List all non-dead concepts")

    # add
    add_p = subparsers.add_parser("add", help="Add a new concept")
    add_p.add_argument("--name", required=True)
    add_p.add_argument("--desc", required=True)
    add_p.add_argument("--vertical", required=True)
    add_p.add_argument("--platform", required=True)
    add_p.add_argument("--notes", default="")

    # promote
    promo_p = subparsers.add_parser("promote", help="Promote a concept with performance metrics")
    promo_p.add_argument("--id", required=True)
    promo_p.add_argument("--hook-rate", type=float, required=True)
    promo_p.add_argument("--ctr", type=float, required=True)
    promo_p.add_argument("--cpa", type=float, required=True)

    # kill
    kill_p = subparsers.add_parser("kill", help="Kill a concept")
    kill_p.add_argument("--id", required=True)

    args = parser.parse_args()

    if args.command == "list":
        concepts = get_all()
        if not concepts:
            print("No concepts in vault yet.")
            return
        proven = [c for c in concepts if c["status"] in ("proven", "scaling")]
        testing = [c for c in concepts if c["status"] == "testing"]
        if proven:
            print(f"\n✅ PROVEN / SCALING ({len(proven)})")
            for c in proven:
                _print_concept(c)
        if testing:
            print(f"\n🧪 TESTING ({len(testing)})")
            for c in testing:
                _print_concept(c)
        total = len(load_vault())
        dead = total - len(concepts)
        print(f"\nTotal: {total} concepts ({dead} dead)")

    elif args.command == "add":
        concept = add_concept(
            name=args.name,
            description=args.desc,
            vertical=args.vertical,
            platform=args.platform,
            notes=args.notes,
        )
        print(f"[VAULT] Added concept {concept['concept_id']}: {concept['name']}")
        _print_concept(concept)

    elif args.command == "promote":
        result = promote(args.id, args.hook_rate, args.ctr, args.cpa)
        if result:
            _print_concept(result)

    elif args.command == "kill":
        kill(args.id)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()

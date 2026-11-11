"""Regression check for the challenger-brief parser in deploy.py.

Runs standalone (`python scripts/test_deploy_parser.py`) and under pytest.
The fixture is the INDENTED format prompts.yaml asks the generator for,
because that is what a live cycle produces.

Before the fix, the shared extract() helper matched every pattern with
re.DOTALL, so `CHALLENGER NAME:` swallowed the whole brief and the deployed
ad name became the entire document.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from deploy import parse_challenger_brief  # noqa: E402

BRIEF = """  CHALLENGER NAME: hook-question-rate-hike-v1
  HYPOTHESIS: The baseline hook is a statement; testing a question hook.
  ANGLE: Rate Hike Outrage (Angle #4 — Problem-Aware Agitation)
  HOOK TYPE: Question Hook (#9)
  AWARENESS STAGE: 3

  --- HOOK ---
  Did your car insurance just quietly go up — without you filing a single claim?

  --- BODY ---
  Most people don't notice until renewal.
  Takes 2 minutes to compare.

  --- CTA ---
  See what your neighbors are paying →

  --- FORMAT ---
  Type: static (image: frustrated person opening insurance bill)
  Length: N/A
  Persona: N/A (no presenter — copy-forward)

  --- STRATEGIC RATIONALE ---
  Previous baseline used a loyalty-tax statement hook. Testing question hook.
"""


def test_parse_challenger_brief():
    p = parse_challenger_brief(BRIEF)

    assert p["name"] == "hook-question-rate-hike-v1", p["name"]
    assert p["angle"] == "Rate Hike Outrage (Angle #4 — Problem-Aware Agitation)", p["angle"]
    assert p["hook_type"] == "Question Hook (#9)", p["hook_type"]
    assert p["awareness_stage"] == "3", p["awareness_stage"]
    assert p["hook"] == (
        "Did your car insurance just quietly go up — without you filing a single claim?"
    ), p["hook"]
    assert p["body"].startswith("Most people don't notice until renewal."), p["body"]
    assert p["cta"] == "See what your neighbors are paying →", p["cta"]
    assert p["format_type"] == (
        "static (image: frustrated person opening insurance bill)"
    ), p["format_type"]
    assert "Previous baseline used" in p["rationale"], p["rationale"]

    # the old parser ran past the newline and swallowed the rest of the brief
    assert "\n" not in p["name"]
    assert "\n" not in p["angle"]
    assert "\n" not in p["hook_type"]
    assert "\n" not in p["awareness_stage"]


if __name__ == "__main__":
    test_parse_challenger_brief()
    print("parser ok")
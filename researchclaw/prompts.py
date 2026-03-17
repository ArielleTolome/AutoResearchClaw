"""Prompt externalization for the ResearchClaw pipeline.

All 23 stage prompts are defined here as defaults and can be overridden
via a user-provided YAML file.  Users customize prompts without touching
Python source code.

Architecture
------------
* ``_DEFAULT_STAGES`` — every LLM-facing prompt, keyed by stage name.
* ``_DEFAULT_BLOCKS`` — reusable prompt fragments (topic constraint, etc.).
* ``_DEFAULT_SUB_PROMPTS`` — secondary prompts (code repair, etc.).
* ``PromptManager`` — loads defaults → merges user overrides → renders templates.
* ``_render()`` — safe ``{variable}`` substitution that leaves unmatched
  patterns (JSON schemas, curly-brace literals) untouched.

Usage
-----
::

    from researchclaw.prompts import PromptManager

    pm = PromptManager()                           # defaults only
    pm = PromptManager("my_prompts.yaml")          # with user overrides

    sp = pm.for_stage("topic_init", topic="RL for drug discovery", domains="ml, bio")
    resp = llm.chat(
        [{"role": "user", "content": sp.user}],
        system=sp.system,
        json_mode=sp.json_mode,
        max_tokens=sp.max_tokens,
    )
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


def _render(template: str, variables: dict[str, str]) -> str:
    """Replace ``{var_name}`` placeholders with *variables* values.

    Only bare ``{word_chars}`` tokens are substituted — JSON schema
    examples like ``{candidates:[...]}`` or ``{score_1_to_10:number}``
    are left untouched because the regex requires the closing ``}``
    immediately after the identifier.
    """

    def _replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(variables[key]) if key in variables else match.group(0)

    return re.sub(r"\{(\w+)\}", _replacer, template)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderedPrompt:
    """Fully rendered prompt ready for ``llm.chat()``."""

    system: str
    user: str
    json_mode: bool = False
    max_tokens: int | None = None


# ---------------------------------------------------------------------------
# PromptManager
# ---------------------------------------------------------------------------


class PromptManager:
    """Central registry for pipeline prompts with optional YAML overrides."""

    def __init__(self, overrides_path: str | Path | None = None) -> None:
        # Deep-copy defaults so mutations don't leak across instances
        self._stages: dict[str, dict[str, Any]] = {
            k: dict(v) for k, v in _DEFAULT_STAGES.items()
        }
        self._blocks: dict[str, str] = dict(_DEFAULT_BLOCKS)
        self._sub_prompts: dict[str, dict[str, Any]] = {
            k: dict(v) for k, v in _DEFAULT_SUB_PROMPTS.items()
        }
        if overrides_path:
            self._load_overrides(Path(overrides_path))

    # -- loading ----------------------------------------------------------

    def _load_overrides(self, path: Path) -> None:
        if not path.exists():
            logger.warning("Prompts file not found: %s — using defaults", path)
            return
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.warning("Bad prompts YAML %s: %s — using defaults", path, exc)
            return

        for stage_name, stage_data in (data.get("stages") or {}).items():
            if stage_name in self._stages and isinstance(stage_data, dict):
                self._stages[stage_name].update(stage_data)
            else:
                logger.warning("Unknown stage in prompts file: %s", stage_name)

        for block_name, block_text in (data.get("blocks") or {}).items():
            if isinstance(block_text, str):
                self._blocks[block_name] = block_text

        for sub_name, sub_data in (data.get("sub_prompts") or {}).items():
            if sub_name in self._sub_prompts and isinstance(sub_data, dict):
                self._sub_prompts[sub_name].update(sub_data)

        logger.info("Loaded prompt overrides from %s", path)

    # -- primary API ------------------------------------------------------

    def for_stage(
        self,
        stage: str,
        *,
        evolution_overlay: str = "",
        **kwargs: Any,
    ) -> RenderedPrompt:
        """Return a fully rendered prompt for *stage* with variables filled.

        If *evolution_overlay* is provided, it is appended to the user prompt
        so the LLM can learn from prior run lessons.
        """
        entry = self._stages[stage]
        kw = {k: str(v) for k, v in kwargs.items()}
        user_text = _render(entry["user"], kw)
        if evolution_overlay:
            user_text = f"{user_text}\n\n{evolution_overlay}"
        return RenderedPrompt(
            system=_render(entry["system"], kw),
            user=user_text,
            json_mode=entry.get("json_mode", False),
            max_tokens=entry.get("max_tokens"),
        )

    def system(self, stage: str) -> str:
        """Return the raw system prompt template for *stage*."""
        return self._stages[stage]["system"]

    def user(self, stage: str, **kwargs: Any) -> str:
        """Return the rendered user prompt for *stage*."""
        return _render(
            self._stages[stage]["user"],
            {k: str(v) for k, v in kwargs.items()},
        )

    def json_mode(self, stage: str) -> bool:
        return self._stages[stage].get("json_mode", False)

    def max_tokens(self, stage: str) -> int | None:
        return self._stages[stage].get("max_tokens")

    # -- blocks -----------------------------------------------------------

    def block(self, name: str, **kwargs: Any) -> str:
        """Render a reusable prompt block."""
        return _render(
            self._blocks[name],
            {k: str(v) for k, v in kwargs.items()},
        )

    # -- sub-prompts (code repair, etc.) ----------------------------------

    def sub_prompt(self, name: str, **kwargs: Any) -> RenderedPrompt:
        """Return a rendered sub-prompt (e.g. code_repair)."""
        entry = self._sub_prompts[name]
        kw = {k: str(v) for k, v in kwargs.items()}
        return RenderedPrompt(
            system=_render(entry["system"], kw),
            user=_render(entry["user"], kw),
        )

    # -- introspection ----------------------------------------------------

    def stage_names(self) -> list[str]:
        return list(self._stages.keys())

    def has_stage(self, stage: str) -> bool:
        return stage in self._stages

    def export_yaml(self, path: Path) -> None:
        """Write current prompts (defaults + overrides) to a YAML file."""
        data: dict[str, Any] = {
            "version": "1.0",
            "blocks": dict(self._blocks),
            "stages": {k: dict(v) for k, v in self._stages.items()},
            "sub_prompts": {k: dict(v) for k, v in self._sub_prompts.items()},
        }
        path.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True, width=120),
            encoding="utf-8",
        )


# ========================================================================
# DEFAULT PROMPTS — edit prompts.yaml to override; do NOT edit these.
# ========================================================================

# -- Reusable blocks -----------------------------------------------------

_DEFAULT_BLOCKS: dict[str, str] = {
    "title_guidelines": (
        "\n## Title & Framing Guidelines (from top-conference best practices)\n"
        "Your title and framing MUST satisfy these criteria:\n"
        "1. **Signal novelty**: The title should hint at what is NEW, not just what\n"
        "   was studied. Bad: 'Comparing X and Y'. Good: 'X Outperforms Y Under Z:\n"
        "   A Controlled Analysis'\n"
        "2. **Be specific and concrete**: Avoid generic titles. Include the key finding\n"
        "   or method name. Under 15 words.\n"
        "3. **No abbreviations** in the title unless universally known (e.g., 'LLM' is OK)\n"
        "4. **Memeability test** (Evan Pu): Would a reader enjoy telling a colleague\n"
        "   about this paper's key message?\n"
        "5. **Successful patterns**:\n"
        "   - '[Finding]: [Evidence]' — e.g., 'Momentum SGD Dominates Adaptive Methods\n"
        "     on Multimodal Landscapes: A Controlled Noise-Dimension Study'\n"
        "   - '[Method Name]: [What it does]' — e.g., 'SNR-Calibrated Benchmarking:\n"
        "     Fair Optimizer Comparison Under Controlled Gradient Noise'\n"
        "6. **Avoid pure benchmark papers** unless the methodology is novel. Frame the\n"
        "   contribution as a new INSIGHT or TOOL, not just 'we compared things'\n"
        "\n"
        "IMPORTANT: If the research topic is a well-studied area (e.g., 'comparing\n"
        "optimizers'), reframe it around a NOVEL ANGLE:\n"
        "- A new evaluation methodology\n"
        "- A surprising finding\n"
        "- A practical tool or decision guide\n"
    ),
    "abstract_structure": (
        "\nAbstract MUST follow this 5-sentence structure (150-200 words total):\n"
        "(1) The problem and why it matters\n"
        "(2) How others approach it and their limitations\n"
        "(3) Your approach and what makes it novel\n"
        "(4) Key results WITH 2-3 specific numbers (mean +/- std only — NO per-seed ranges)\n"
        "(5) The implication / takeaway\n"
        "FORBIDDEN in abstract: per-seed value ranges (e.g., '0.71-0.73 across seeds'), "
        "lists of more than 3 numbers, or repeating full results tables.\n"
    ),
    "compute_budget": (
        "\n## Compute Budget Constraint\n"
        "- Total execution time limit: {time_budget_sec} seconds\n"
        "- You MUST design experiments that complete within this budget\n"
        "- Estimate: a simple numpy loop runs ~10M iterations/sec; a nested loop over\n"
        "  conditions runs proportionally slower\n"
        "- SCALING RULES (mandatory):\n"
        "  - If total conditions > 100: reduce seeds to 3-5 (not 20)\n"
        "  - If total conditions > 500: reduce to 2-3 representative conditions per factor\n"
        "  - If time_budget < 300s: limit total optimization steps to ≤5,000 per run\n"
        "  - If time_budget < 120s: limit total optimization steps to ≤1,000 per run\n"
        "  - Always print intermediate results so partial data is captured on timeout\n"
        "- MANDATORY: print a 'TIME_ESTIMATE: Xs' line before the main loop,\n"
        "  estimating total runtime based on a small pilot (run 1 condition, extrapolate)\n"
        "- MANDATORY: implement a time guard — check elapsed time periodically and\n"
        "  stop gracefully if approaching 80% of budget, saving all results collected so far\n"
        "- MANDATORY: add NaN/divergence fast-fail guard:\n"
        "  - After each optimization step, check if loss is NaN or > 100\n"
        "  - If detected, print 'FAIL: NaN/divergence detected', save partial results, and exit\n"
        "  - Do NOT waste compute on a diverging run\n"
        "- RECOMMENDED: use the experiment_harness module (pre-installed in sandbox):\n"
        "  ```\n"
        "  from experiment_harness import ExperimentHarness\n"
        "  harness = ExperimentHarness(time_budget={time_budget_sec})\n"
        "  # In your experiment loop:\n"
        "  if harness.should_stop():\n"
        "      break  # graceful stop at 80% of budget\n"
        "  harness.report_metric('metric_name', value)  # validated output\n"
        "  harness.finalize()  # writes results.json\n"
        "  ```\n"
    ),
    "topic_constraint": (
        "\n\n=== HARD TOPIC CONSTRAINT ===\n"
        "The paper MUST be about: {topic}\n"
        "PROHIBITED content (unless user explicitly specifies case-study mode):\n"
        "- Do NOT treat environment setup, dependency installation, or infrastructure "
        "failures as a research contribution.\n"
        "- Do NOT present debugging logs, system errors, or configuration issues "
        "as experimental findings.\n"
        "- Do NOT drift to tangential topics not directly related to the stated topic.\n"
        "- Every section MUST connect back to the core research question.\n"
        "- The Abstract and Introduction MUST clearly state the research problem "
        "derived from: {topic}\n"
        "- The Method section MUST describe a technical approach, not a workflow.\n"
        "- The Results section MUST report quantitative outcomes of experiments, "
        "not environment status.\n"
        "=== END CONSTRAINT ===\n"
    ),
    "pkg_hint_sandbox": (
        "\nAVAILABLE PACKAGES (sandbox mode): Python stdlib, numpy, math, random, "
        "statistics, json.\n"
        "Do NOT use: torch, tensorflow, jax, sklearn, pandas, scipy, matplotlib, "
        "or any deep learning framework.\n"
        "Write the experiment using ONLY numpy and stdlib.\n"
    ),
    "dataset_guidance": (
        "\n## Standard Datasets & Real Baselines (MANDATORY when applicable)\n"
        "If your experiment involves image classification, distribution shift, "
        "normalization, data augmentation, or transfer learning, you MUST use "
        "standard benchmark datasets — NOT synthetic torch.randn() data.\n\n"
        "AVAILABLE DATASETS (pre-cached in sandbox, no download needed):\n"
        "- `torchvision.datasets.CIFAR10(root='/workspace/data', train=True/False, download=False)`\n"
        "- `torchvision.datasets.FashionMNIST(root='/workspace/data', train=True/False, download=False)`\n"
        "- For other torchvision datasets: use `download=True` (network available during setup)\n\n"
        "DISTRIBUTION SHIFT — use torchvision corruption transforms:\n"
        "- Gaussian noise: `transforms.Lambda(lambda x: x + torch.randn_like(x) * sigma)`\n"
        "- Brightness shift: `transforms.ColorJitter(brightness=0.5)`\n"
        "- Contrast shift: `transforms.ColorJitter(contrast=0.5)`\n"
        "- Blur: `transforms.GaussianBlur(kernel_size=5, sigma=(0.1, 2.0))`\n"
        "- For CIFAR-10-C style corruptions, apply transforms to test set only.\n\n"
        "DATA PATH: Always use `/workspace/data` as the data root directory.\n"
        "If running outside Docker, fall back to `./data` with download=True.\n\n"
        "REAL BASELINES & MODERN BENCHMARKS (CRITICAL):\n"
        "- Use proper train/test splits from the dataset (never split randomly in code)\n"
        "- Use standard architectures (ResNet-18/20, simple CNN) — not toy 2-layer MLPs\n"
        "- Report standard metrics (top-1 accuracy for classification tasks)\n"
        "- Compare against published baselines where available\n"
        "- BASELINES MUST BE CURRENT: Use baselines that are widely adopted in recent "
        "top-venue papers (2023-2026). Do NOT use outdated or obsolete methods as the "
        "primary comparison. Examples of outdated baselines to AVOID as sole comparison:\n"
        "  * AlexNet, VGG-16 (use ResNet-50, ViT, ConvNeXt instead)\n"
        "  * Vanilla SGD without momentum (use AdamW, SGD+momentum+cosine LR)\n"
        "  * Simple RNN/LSTM for NLP (use Transformer-based models)\n"
        "  * Vanilla GAN (use StyleGAN, Diffusion models)\n"
        "- Include at LEAST one strong, modern baseline that represents current SOTA "
        "or near-SOTA. Beating only weak/outdated baselines is NOT a valid contribution.\n"
        "- BENCHMARKS MUST BE STANDARD: Use benchmarks that are actively used in the "
        "community. Check recent papers in the field to confirm the benchmark is current.\n"
        "  * For image classification: CIFAR-10/100, ImageNet, or domain-specific standard\n"
        "  * For NLP: GLUE/SuperGLUE, MMLU, or task-specific standard\n"
        "  * For RL: MuJoCo, Atari, or domain-specific standard\n"
        "  * AVOID benchmarks that are no longer used in the community\n\n"
        "WHEN TO USE SYNTHETIC DATA (rare, justified cases only):\n"
        "- Theoretical analysis of optimization landscapes\n"
        "- Controlled ablation of a specific mathematical property\n"
        "- Problems with no standard dataset (e.g., novel combinatorial domains)\n"
        "- In these cases, explain WHY synthetic data is appropriate and cite relevant "
        "precedents.\n"
    ),
    "hp_reporting": (
        "\n## Hyperparameter Reporting (MANDATORY)\n"
        "At the TOP of main.py, define a HYPERPARAMETERS dictionary containing ALL "
        "tunable hyperparameters used in your experiment:\n"
        "```python\n"
        "HYPERPARAMETERS = {\n"
        "    'learning_rate': 0.001,\n"
        "    'batch_size': 64,\n"
        "    'num_epochs': 50,\n"
        "    'hidden_dim': 256,\n"
        "    # ... all other hyperparameters\n"
        "}\n"
        "```\n"
        "At the end of main.py, save hyperparameters to results.json:\n"
        "```python\n"
        "import json\n"
        "results = {'hyperparameters': HYPERPARAMETERS, 'metrics': collected_metrics}\n"
        "with open('results.json', 'w') as f:\n"
        "    json.dump(results, f, indent=2)\n"
        "```\n"
        "EVERY hyperparameter must be used in the code — no dead parameters.\n"
        "The paper MUST include a hyperparameter table — this data feeds into it.\n"
    ),
    "writing_structure": (
        "\n## Paper Section Writing Rules\n"
        "ABSTRACT (150-200 words, 5-sentence structure):\n"
        "- (1) Problem and significance (2) Prior approaches and gaps\n"
        "- (3) Your approach and novelty (4) Key results with 2-3 specific numbers\n"
        "- (5) Implication/takeaway\n"
        "- Do NOT list per-seed ranges (e.g., '0.71-0.73 across seeds') — use mean +/- std\n"
        "- Do NOT repeat numbers that appear in the Results section — pick the 2-3 most impactful\n\n"
        "INTRODUCTION (800-1200 words):\n"
        "- Paragraph 1: Problem motivation (why this matters)\n"
        "- Paragraph 2: What exists and why it falls short\n"
        "- Paragraph 3: Your approach and key insight\n"
        "- Paragraph 4: Contributions (2-3 bullet points)\n\n"
        "RELATED WORK (600-900 words):\n"
        "- Organize by sub-topic, not chronologically\n"
        "- End each paragraph with how your work differs\n"
        "- Cite at least 15 references, all directly relevant\n\n"
        "METHOD (800-1200 words):\n"
        "- Full algorithm description (pseudocode or step-by-step)\n"
        "- All hyperparameters with values and justification\n"
        "- Architecture details sufficient for reproduction\n\n"
        "RESULTS (800-1200 words):\n"
        "- Do NOT repeat the same number more than twice across the paper\n"
        "- Each number in a table should be discussed AT MOST once in text\n"
        "- Tables: mean +/- std with 95%% CI in parentheses\n"
        "- Bold the best result in each column\n"
        "- Every comparison claim must cite a p-value\n\n"
        "LIMITATIONS (200-400 words, 3-5 points):\n"
        "- State each limitation ONCE, here only — not scattered throughout\n"
        "- No disclaimers like 'due to computational constraints'\n\n"
        "CONCLUSION (200-300 words):\n"
        "- Summarize findings (match actual results, no aspirational claims)\n"
        "- 2-3 sentences of future work\n"
    ),
}

# -- Debate role prompts (multi-perspective generation) -------------------

DEBATE_ROLES_HYPOTHESIS: dict[str, dict[str, str]] = {
    "innovator": {
        "system": (
            "You are a bold, creative researcher who thinks outside the box. "
            "You pursue high-risk high-reward ideas, draw cross-domain analogies, "
            "and propose counter-intuitive hypotheses that challenge mainstream thinking."
        ),
        "user": (
            "Generate at least 2 novel, unconventional hypotheses from the synthesis below.\n"
            "For each hypothesis provide:\n"
            "- A bold claim that pushes boundaries\n"
            "- Cross-domain inspiration (if applicable)\n"
            "- Rationale grounded in the literature gaps\n"
            "- Measurable prediction and failure condition\n"
            "- Estimated risk level (low/medium/high)\n\n"
            "Topic: {topic}\n"
            "Synthesis:\n{synthesis}"
        ),
    },
    "pragmatist": {
        "system": (
            "You are a practical ML engineer focused on what actually works. "
            "You prioritize computational feasibility, engineering simplicity, "
            "reliable baselines, and incremental but solid improvements."
        ),
        "user": (
            "Generate at least 2 feasible, well-grounded hypotheses from the synthesis below.\n"
            "For each hypothesis provide:\n"
            "- A concrete, testable claim with clear methodology\n"
            "- Why this is achievable with limited compute\n"
            "- Rationale based on proven techniques\n"
            "- Measurable prediction and failure condition\n"
            "- Resource requirements estimate\n\n"
            "Topic: {topic}\n"
            "Synthesis:\n{synthesis}"
        ),
    },
    "contrarian": {
        "system": (
            "You are a rigorous devil's advocate who challenges assumptions. "
            "You find blind spots, hidden failure modes, and counter-evidence. "
            "Your value is in finding problems others ignore. Be provocative "
            "but always grounded in evidence."
        ),
        "user": (
            "Critically examine the synthesis and generate at least 2 contrarian hypotheses.\n"
            "For each hypothesis provide:\n"
            "- A challenge to a widely-held assumption in this area\n"
            "- Evidence or reasoning for why the mainstream view may be wrong\n"
            "- An alternative hypothesis that accounts for overlooked factors\n"
            "- Measurable prediction and failure condition\n"
            "- Potential negative results that would be informative\n\n"
            "Topic: {topic}\n"
            "Synthesis:\n{synthesis}"
        ),
    },
}

DEBATE_ROLES_ANALYSIS: dict[str, dict[str, str]] = {
    "optimist": {
        "system": (
            "You highlight positive findings, promising extensions, and silver linings "
            "in experimental results. You identify what worked well and why, "
            "and suggest how to build on successes."
        ),
        "user": (
            "Analyze the experiment results from an optimistic perspective.\n"
            "Cover:\n"
            "- What worked well and why\n"
            "- Unexpected positive findings\n"
            "- Promising extensions and next steps\n"
            "- Silver linings in any negative results\n\n"
            "{preamble}\n{data_context}\n"
            "Run context:\n{context}"
        ),
    },
    "skeptic": {
        "system": (
            "You question the significance of results with maximum rigor. "
            "You check statistical validity, identify confounds, and demand "
            "stronger evidence. Every claim must earn its place."
        ),
        "user": (
            "Critically scrutinize the experiment results.\n"
            "Cover:\n"
            "- Statistical concerns (significance, sample size, multiple comparisons)\n"
            "- Potential confounds and alternative explanations\n"
            "- Missing evidence or controls\n"
            "- Whether metrics truly capture the intended phenomenon\n\n"
            "{preamble}\n{data_context}\n"
            "Run context:\n{context}"
        ),
    },
    "methodologist": {
        "system": (
            "You scrutinize HOW experiments were conducted. You audit "
            "internal/external validity, reproducibility, baseline fairness, "
            "and evaluation protocols."
        ),
        "user": (
            "Audit the experimental methodology.\n"
            "Cover:\n"
            "- Baseline fairness and completeness\n"
            "- Metric appropriateness for the research question\n"
            "- Evaluation protocol (data leakage, contamination risks)\n"
            "- Ablation completeness\n"
            "- Reproducibility assessment\n"
            "- Specific methodology improvements needed\n\n"
            "{preamble}\n{data_context}\n"
            "Run context:\n{context}"
        ),
    },
}

# -- Sub-prompts (secondary LLM calls within a stage) --------------------

_DEFAULT_SUB_PROMPTS: dict[str, dict[str, Any]] = {
    "hypothesis_synthesize": {
        "system": (
            "You are a senior research director synthesizing multiple perspectives "
            "into a decisive research proposal. The best synthesis is not a "
            "compromise but takes the strongest elements from each viewpoint. "
            "Preserve genuine disagreements — do not flatten controversy."
        ),
        "user": (
            "Below are hypotheses generated from three different research perspectives.\n"
            "Synthesize them into a final set of 2-4 hypotheses that:\n"
            "1. Take the strongest, most novel ideas\n"
            "2. Address critical concerns raised by the contrarian\n"
            "3. Ensure feasibility (pragmatist's input)\n"
            "4. Note unresolved disagreements between perspectives\n"
            "5. For each final hypothesis: rationale, measurable prediction, "
            "failure condition\n\n"
            "{perspectives}"
        ),
    },
    "analysis_synthesize": {
        "system": (
            "You are a senior research director synthesizing multiple analytical "
            "perspectives into a comprehensive assessment. Find the truth — if "
            "the skeptic or methodologist raise valid concerns, acknowledge them. "
            "Do not suppress criticism."
        ),
        "user": (
            "Below are analyses from three different perspectives (optimist, "
            "skeptic, methodologist).\n"
            "Produce a unified analysis that:\n"
            "1. Identifies consensus points (high-confidence conclusions)\n"
            "2. Resolves conflicts with evidence-based judgment\n"
            "3. Rates result quality (1-10 with justification)\n"
            "4. Lists 3-5 key findings\n"
            "5. Notes methodology gaps that need addressing\n"
            "6. Gives a clear PROCEED/PIVOT/REFINE recommendation\n\n"
            "Required sections: Metrics Summary, Consensus Findings, "
            "Contested Points, Statistical Checks, Methodology Audit, "
            "Limitations, Conclusion.\n\n"
            "{perspectives}"
        ),
        "max_tokens": 8192,
    },
    "code_repair": {
        "system": "You fix Python code validation errors while preserving functionality.",
        "user": (
            "The file `{fname}` in the experiment project has validation errors. "
            "Fix ALL issues and return ONLY the corrected file.\n\n"
            "## Validation Issues in {fname}\n{issues_text}\n\n"
            "## All Project Files\n{all_files_ctx}\n\n"
            "IMPORTANT: Do NOT use subprocess, os.system, eval, exec, or any "
            "network/shell calls.\n"
            "Return ONLY the corrected code for `{fname}`."
        ),
    },
    "iterative_improve": {
        "system": (
            "You improve experiment projects and return valid executable Python code. "
            "Use ```filename:xxx.py format for each file."
        ),
        "user": (
            "Improve the experiment code based on prior run results.\n"
            "Return the improved files using ```filename:xxx.py format for each file.\n"
            "Primary metric key: {metric_key}\n"
            "Metric direction: {metric_direction}\n"
            "Do not use subprocess, os.system, eval, exec, or any network/shell calls.\n\n"
            "TOPIC-CODE ALIGNMENT CHECK (do this FIRST):\n"
            "The research topic is: {topic}\n"
            "Before making incremental improvements, verify that the current code "
            "actually implements an experiment relevant to this topic. If the code "
            "is fundamentally unrelated to the topic (e.g., a generic optimizer "
            "when the topic is about multi-agent simulation, or a trivial function "
            "when a complex simulation is needed), REWRITE the code from scratch "
            "to match the topic. Do NOT incrementally improve irrelevant code.\n\n"
            "{condition_coverage_hint}"
            "Current project files:\n{files_context}\n"
            "Run summaries (JSON):\n{run_summaries}"
        ),
        "max_tokens": 8192,
    },
    "iterative_repair": {
        "system": "You fix Python validation issues without adding unsafe behavior.",
        "user": (
            "Fix all validation issues in main.py and return corrected Python code only.\n\n"
            "## Validation Issues\n{issue_text}\n\n"
            "## All Project Files\n{all_files_ctx}"
        ),
    },
}

# -- Stage prompts (one entry per LLM-calling stage) ---------------------

_DEFAULT_STAGES: dict[str, dict[str, Any]] = {
    # ── Phase A: Research Scoping ────────────────────────────────────────
    "topic_init": {
        "system": (
            "You are a rigorous research planner who identifies NOVEL, TIMELY "
            "research angles. You follow recent trends from top ML conferences "
            "(NeurIPS, ICML, ICLR 2024-2026) and propose research that advances "
            "the frontier rather than repeating known results.\n\n"
            "NOVELTY PRINCIPLES:\n"
            "- A good research angle addresses a GAP not yet covered by existing work.\n"
            "- Avoid pure benchmark/comparison studies unless the methodology is novel.\n"
            "- Prefer angles that combine existing techniques in new ways, apply methods "
            "to underexplored domains, or challenge common assumptions.\n"
            "- The research must be FEASIBLE with limited compute (single GPU, hours not days).\n"
            "- Check: would a reviewer say 'this is already well-known'? If so, find a sharper angle."
        ),
        "user": (
            "Create a SMART research goal in markdown.\n"
            "Topic: {topic}\n"
            "Domains: {domains}\n"
            "Project: {project_name}\n"
            "Quality threshold: {quality_threshold}\n\n"
            "Required sections:\n"
            "- **Topic**: The broad area\n"
            "- **Novel Angle**: What specific aspect has NOT been well-studied? "
            "Why is this timely NOW (2024-2026)? What recent development creates "
            "an opportunity? How does this differ from standard approaches?\n"
            "- **Scope**: Focused enough for a single paper\n"
            "- **SMART Goal**: Specific, Measurable, Achievable, Relevant, Time-bound\n"
            "- **Constraints**: Compute budget, available tools, data access\n"
            "- **Success Criteria**: What results would make this publishable?\n"
            "- **Generated**: Timestamp\n\n"
            "IMPORTANT: The 'Novel Angle' section must convincingly argue why this "
            "specific research direction is NOT already covered by existing work. "
            "If the topic is well-studied (e.g., 'comparing optimizers'), you MUST "
            "find a specific unexplored aspect (e.g., 'under distribution shift with "
            "noisy gradients', 'in the few-shot regime', 'with modern architectures')."
        ),
    },
    "problem_decompose": {
        "system": "You are a senior research strategist.",
        "user": (
            "Decompose this research problem into at least 4 prioritized "
            "sub-questions.\n"
            "Topic: {topic}\n"
            "Output markdown with sections: Source, Sub-questions, Priority "
            "Ranking, Risks.\n"
            "Goal context:\n{goal_text}"
        ),
    },
    # ── Phase B: Literature Discovery ────────────────────────────────────
    "search_strategy": {
        "system": (
            "You design literature retrieval strategies and source verification plans."
        ),
        "user": (
            "Create a merged search strategy package.\n"
            "Return a JSON object with keys: search_plan_yaml, sources.\n"
            "search_plan_yaml must be valid YAML text.\n"
            "sources must include id,name,type,url,status,query,verified_at.\n"
            "Topic: {topic}\n"
            "Problem tree:\n{problem_tree}"
        ),
        "json_mode": True,
    },
    "literature_collect": {
        "system": "You are a literature mining assistant.",
        "user": (
            "Generate candidate papers from the search plan.\n"
            "Return JSON: {candidates:[...]} with >=8 rows.\n"
            "Each candidate must include id,title,source,url,year,abstract,"
            "collected_at.\n"
            "Topic: {topic}\n"
            "Search plan:\n{plan_text}"
        ),
        "json_mode": True,
    },
    "ads_literature_collect": {
        "system": (
            "You are an expert ad creative intelligence analyst. "
            "You synthesize competitor ad creative data, audience language, "
            "and market intelligence for performance marketing campaigns."
        ),
        "user": (
            "Generate 12 ad creative intelligence candidates for the following topic.\n"
            "Return JSON: {{\"candidates\": [...]}}\n"
            "Each candidate must have: id, title, source, source_type, url, year, abstract, "
            "awareness_stage, emotional_driver, collected_at.\n\n"
            "source_type must be one of: ad_creative, audience_language, competitor_intel, trend_signal\n"
            "awareness_stage must be one of: unaware, problem_aware, solution_aware, product_aware, most_aware\n"
            "abstract should be 100-300 words of actual ad copy, hook examples, audience pain points, "
            "or competitor messaging angles — real creative intelligence, not descriptions.\n\n"
            "Topic: {topic}\n\n"
            "Generate diverse candidates covering:\n"
            "- 4 competitor ad hooks and copy angles\n"
            "- 3 audience pain point and desire statements (their exact language)\n"
            "- 3 objection/skepticism patterns common in this niche\n"
            "- 2 trend signals or market shifts relevant to this offer"
        ),
        "json_mode": True,
    },
    "literature_screen": {
        "system": (
            "You are a strict domain-aware reviewer with zero tolerance for "
            "cross-domain false positives. You MUST reject papers that are "
            "from unrelated fields, even if they share superficial keyword "
            "overlap. A paper about 'normalization in database systems' is "
            "NOT relevant to 'normalization in deep learning'. A paper about "
            "'graph theory in social networks' is NOT relevant to 'graph "
            "neural networks for molecular property prediction'."
        ),
        "user": (
            "Perform merged relevance+quality screening and return shortlist.\n"
            "Return JSON: {shortlist:[...]} each with title, cite_key "
            "(if present), relevance_score (0-1), quality_score (0-1), "
            "keep_reason.\n"
            "Preserve all original fields (paper_id, doi, arxiv_id, cite_key, "
            "etc.) from the input.\n"
            "Topic: {topic}\n"
            "Domains: {domains}\n"
            "Threshold: {quality_threshold}\n\n"
            "SCREENING RULES (apply strictly):\n"
            "1. DOMAIN MATCH: The paper's actual research domain must match "
            "the topic's domain. Shared keywords across domains do NOT count.\n"
            "2. METHOD RELEVANCE: The paper must discuss methods, benchmarks, "
            "or findings directly applicable to the research topic.\n"
            "3. CROSS-DOMAIN REJECTION: Reject papers from unrelated fields "
            "(e.g., wireless communications, database systems, social science) "
            "even if they use similar terminology.\n"
            "4. RECENCY PREFERENCE: Prefer papers from 2020+ for methodology, "
            "but accept foundational papers (pre-2020) if they introduced key "
            "techniques still in use today.\n"
            "5. SEMINAL PAPERS: Papers marked as source='seminal_library' are "
            "pre-vetted foundational references — keep them if their keywords "
            "match the topic (relevance_score >= 0.7).\n"
            "6. QUALITY FLOOR: Reject papers with no abstract, no venue, and "
            "no citation count (likely not real papers).\n"
            "Candidates JSONL:\n{candidates_text}"
        ),
        "json_mode": True,
    },
    "knowledge_extract": {
        "system": "You extract high-signal evidence cards from papers.",
        "user": (
            "Extract structured knowledge cards from shortlist.\n"
            "Return JSON: {cards:[{card_id,title,cite_key,problem,method,"
            "data,metrics,findings,limitations,citation}]}.\n"
            "IMPORTANT: If the input contains cite_key fields, preserve them "
            "exactly in the output.\n"
            "Shortlist:\n{shortlist}"
        ),
        "json_mode": True,
    },
    # ── Phase C: Knowledge Synthesis ─────────────────────────────────────
    "synthesis": {
        "system": "You are a synthesis specialist for literature reviews.",
        "user": (
            "Produce merged synthesis output (topic clusters + research gaps).\n"
            "Output markdown with sections: Cluster Overview, Cluster 1..N, "
            "Gap 1..N, Prioritized Opportunities.\n"
            "Topic: {topic}\n"
            "Cards context:\n{cards_context}"
        ),
        "max_tokens": 8192,
    },
    "hypothesis_gen": {
        "system": (
            "You formulate testable scientific hypotheses that address gaps "
            "NOT covered by existing literature. Your hypotheses must be:\n"
            "1. NOVEL: Not simply replicating known results or testing obvious things.\n"
            "2. GAP-FILLING: Address specific weaknesses or blind spots identified "
            "in the literature synthesis.\n"
            "3. FEASIBLE: Testable with limited compute (single GPU, <1 day runtime).\n"
            "4. FALSIFIABLE: Have clear failure conditions that would definitively "
            "reject the hypothesis.\n"
            "5. SURPRISING: At least one hypothesis should challenge conventional "
            "wisdom or test a counter-intuitive prediction."
        ),
        "user": (
            "Generate at least 2 falsifiable hypotheses from the synthesis below.\n"
            "For each hypothesis provide:\n"
            "- **Hypothesis statement**: A clear, testable claim\n"
            "- **Novelty argument**: Why this has NOT been tested before, citing "
            "specific gaps from the synthesis\n"
            "- **Rationale**: Theoretical or empirical basis for expecting this result\n"
            "- **Measurable prediction**: Specific quantitative outcome expected\n"
            "- **Failure condition**: What result would reject this hypothesis?\n"
            "- **Required baselines**: What modern, state-of-the-art methods must be "
            "compared against to make the finding meaningful?\n\n"
            "AVOID:\n"
            "- Hypotheses that are trivially obvious (e.g., 'more data improves accuracy')\n"
            "- Hypotheses that replicate well-known results already in the literature\n"
            "- Hypotheses that cannot be tested within the compute budget\n\n"
            "Synthesis:\n{synthesis}"
        ),
    },
    # ── Phase D: Experiment Design ───────────────────────────────────────
    "experiment_design": {
        "system": "You are a principal investigator designing ML experiments.",
        "user": (
            "{preamble}\n\n"
            "Design an experiment plan as YAML.\n"
            "Required keys: objectives,datasets,baselines,proposed_methods,"
            "ablations,metrics,risks,compute_budget.\n\n"
            "NAMING REQUIREMENT (CRITICAL for paper quality):\n"
            "- Every condition name in baselines, proposed_methods, and ablations MUST be "
            "a DESCRIPTIVE algorithm name DERIVED FROM THE HYPOTHESES ABOVE, NOT a generic label.\n"
            "- WRONG: baseline_1, baseline_2, method_variant_1, method_variant_2\n"
            "- WRONG: random_search, bayesian_optimization, ppo_policy, curiosity_driven_rl "
            "(these are generic defaults — NEVER use them unless they are actually what "
            "the hypotheses call for)\n"
            "- RIGHT: names that reflect the specific methods/architectures/algorithms in "
            "the hypotheses (e.g., rim_agent, monolithic_gru, ewc_baseline, sleep_consolidation, "
            "no_sleep_ablation, coarse_routing, fine_routing)\n"
            "- The name should immediately tell a reader WHAT algorithm or strategy is used.\n"
            "- This is critical because these names appear directly in the paper.\n\n"
            "BASELINE & BENCHMARK MODERNITY (CRITICAL for acceptance):\n"
            "- Baselines MUST be modern, widely-adopted methods from recent top-venue "
            "papers (2023-2026). Beating only outdated or weak baselines is NOT a valid "
            "contribution and will result in desk rejection.\n"
            "- Include at LEAST one strong baseline that represents current SOTA or "
            "near-SOTA in the specific sub-area. Check recent NeurIPS/ICML/ICLR papers "
            "to identify appropriate baselines.\n"
            "- Benchmarks MUST be standard and actively used. If a benchmark has been "
            "superseded, use the newer version.\n"
            "- For each baseline, cite the original paper and note why it is a fair "
            "and competitive comparison.\n\n"
            "HYPOTHESIS ALIGNMENT (CRITICAL — most common failure mode):\n"
            "- Your experiment plan MUST directly test the hypotheses listed above.\n"
            "- Each hypothesis should map to at least one comparison between conditions.\n"
            "- Baselines must be the specific alternatives named in the hypotheses, NOT "
            "generic optimization methods like random_search or bayesian_optimization.\n"
            "- If a hypothesis says 'X outperforms Y', then X must be a proposed_method "
            "and Y must be a baseline.\n"
            "- Ablations must isolate the specific components claimed to matter in the "
            "hypotheses (e.g., if hypothesis claims routing helps, ablate routing).\n\n"
            "STABILITY & REPRODUCIBILITY (CRITICAL for RL-based methods):\n"
            "- Under `proposed_methods`, specify key hyperparameters (learning rate, "
            "gradient clip threshold, entropy coefficient, etc.).\n"
            "- Under `risks`, explicitly list numerical stability concerns "
            "(NaN/divergence, reward explosion, policy collapse) and mitigations "
            "(gradient clipping, reward normalization, early stopping on NaN).\n"
            "- Under `metrics`, include:\n"
            "  * Primary metric with direction and units\n"
            "  * `success_rate`: fraction of seeds that complete without NaN/crash\n"
            "  * At least ONE discovery-aligned endpoint (e.g., identification "
            "accuracy, time-to-discovery, final posterior mass on true hypothesis) "
            "in addition to any proxy metric\n"
            "{dataset_guidance}\n\n"
            "- Under `datasets`, specify AT LEAST 2 regime factors to stratify by "
            "(e.g., noise_level: [low, high], hypothesis_space_size: [small, large]). "
            "Results MUST be reported per-regime. A single-regime experiment cannot "
            "support generality claims and will be rejected by reviewers.\n"
            "- FACTORIAL DESIGN PREFERRED: If you vary multiple factors (e.g., scale AND "
            "noise), design a factorial grid (e.g., small+low, small+high, large+low, "
            "large+high) so each factor's effect can be isolated. Bundling factors "
            "(e.g., easy=small+low, hard=large+high) is a confounder and reviewers will "
            "flag it. If computational budget limits the grid, at minimum acknowledge "
            "that factors are bundled and limit claims accordingly.\n"
            "- Under `compute_budget`, plan for minimum 10 seeds per condition to "
            "ensure valid statistical comparisons.\n\n"
            "STATISTICAL POWER REQUIREMENTS (CRITICAL for publishability):\n"
            "- Use AT LEAST 5 random seeds per condition (10 preferred)\n"
            "- Use AT LEAST 30 episodes per seed for RL methods\n"
            "- Report: mean ± std, 95% bootstrap CI, per-seed raw values\n"
            "- For method comparisons: use paired bootstrap or Wilcoxon signed-rank test "
            "(NOT paired t-test with n < 10)\n"
            "- Report effect sizes (Cohen's d or rank-biserial correlation)\n"
            "- 3 seeds is INSUFFICIENT — reviewers will reject papers with n=3\n\n"
            "Hypotheses:\n{hypotheses}"
        ),
    },
    "code_generation": {
        "system": (
            "You are a computational scientist who writes real, runnable "
            "experiments. Your code implements actual algorithms with real "
            "mathematical operations. You NEVER fake results with random number "
            "generators. Always use the ```filename:xxx.py format for each file. "
            "Use numpy for numerical computation. Keep code self-contained "
            "and deterministic."
        ),
        "user": (
            "Generate a Python experiment project for the following research "
            "topic:\n"
            "TOPIC: {topic}\n\n"
            "CRITICAL REQUIREMENTS — your code MUST satisfy ALL of these:\n"
            "1. Implement the ACTUAL experiment described in the topic and "
            "plan below.\n"
            "   If the topic is about simulation (e.g., multi-agent systems, "
            "network dynamics),\n"
            "   write simulation code. If about optimization, write "
            "optimization code.\n"
            "   Match the code to the topic — do NOT default to generic "
            "gradient descent.\n"
            "2. Use proper mathematical models appropriate to the research "
            "question.\n"
            "   Examples: agent-based simulation, graph algorithms, "
            "statistical analysis,\n"
            "   optimization, Monte Carlo methods — whatever fits the topic.\n"
            "3. Run REAL computational experiments with meaningful "
            "parameters.\n"
            "4. Collect REAL metrics that directly answer the research "
            "question.\n"
            "5. The code must be scientifically meaningful — a reviewer should "
            "see\n"
            "   actual implementations relevant to the TOPIC, not a generic "
            "optimizer.\n\n"
            "OUTPUT FORMAT — return multiple files using this exact format:\n"
            "```filename:main.py\n"
            "# entry point code\n"
            "```\n\n"
            "```filename:optimizers.py\n"
            "# optimizer implementations\n"
            "```\n\n"
            "CODE STRUCTURE:\n"
            "- main.py: entry point that runs experiments and prints metrics\n"
            "- main.py MUST begin with a docstring specifying:\n"
            "  (a) Dataset used and how it is loaded\n"
            "  (b) Distribution shift / corruption definition (if applicable)\n"
            "  (c) Model architecture (layers, dimensions, activation)\n"
            "  (d) Training protocol (optimizer, epochs, batch size, LR schedule)\n"
            "  (e) Evaluation protocol (train/test split, metrics computed)\n"
            "- Additional modules for algorithms, objective functions, "
            "utilities\n"
            "- Primary metric key: {metric}\n"
            "- main.py must print metric lines as `name: value` (one per "
            "line)\n"
            "- Use deterministic seeds (numpy.random.seed or random.seed)\n"
            "- No external data files, no network calls, no GPU required\n"
            "- FORBIDDEN: subprocess, os.system, eval, exec, shutil, socket\n"
            "{pkg_hint}\n"
            "ANTI-PATTERNS (do NOT do these):\n"
            "- Do NOT generate random numbers and pretend they are experiment "
            "results\n"
            "- Do NOT use `random.uniform()` to simulate a decreasing loss "
            "curve\n"
            "- Do NOT hardcode metric values or use trivial arithmetic as "
            "metrics\n\n"
            "MULTI-CONDITION REQUIREMENT (CRITICAL):\n"
            "The experiment plan below specifies multiple conditions, treatments, "
            "or strategies to compare. Your code MUST:\n"
            "1. Implement ALL conditions/treatments listed in the experiment plan "
            "— not just one baseline.\n"
            "2. Run each condition independently with the same controlled setup "
            "(same seeds, same initialization, same budget).\n"
            "3. Print metrics with condition labels: "
            "`condition=<name> {metric}: <value>` for EACH condition.\n"
            "4. After all conditions, print a summary comparison line: "
            "`SUMMARY: condition1=<val>, condition2=<val>, ...`\n"
            "5. If the plan has N conditions, the output MUST contain N separate "
            "labeled metric streams. Running only one condition is NOT acceptable.\n"
            "6. BREADTH-FIRST ORDERING: Run ONE representative configuration per "
            "condition FIRST (e.g., default parameters), so that ALL conditions "
            "produce at least one result. Only AFTER all conditions have results, "
            "run additional parameter sweeps if time remains. This prevents the "
            "time budget from being exhausted on condition 1's parameter sweep "
            "while conditions 2..N never execute.\n"
            "7. CONDITION COMPLETENESS: After code generation, mentally verify that "
            "EVERY condition in the experiment plan below has a corresponding code "
            "path. If the plan lists conditions A, B, C, D — your code must handle "
            "all four, not just A, B, C. Missing conditions invalidate the experiment.\n"
            "8. CRASH RESILIENCE: Wrap each condition's execution in a try/except "
            "block so that if one condition crashes (e.g., NaN, timeout, config error), "
            "the remaining conditions still execute. Print `CONDITION_FAILED: <name> "
            "<error>` on failure and continue to the next condition. A partial result "
            "set is far more valuable than a complete crash.\n"
            "9. CONDITION REGISTRY VALIDATION: At startup (before running experiments), "
            "enumerate all condition names and verify each has a valid code path. Print "
            "`REGISTERED_CONDITIONS: <name1>, <name2>, ...` at the top of output. If "
            "any condition is unrecognized, print `MISSING_CONDITION: <name>` and skip "
            "it gracefully rather than raising an exception.\n\n"
            "METRIC DEFINITION REQUIREMENT (CRITICAL):\n"
            "- At the top of main.py, include a docstring or comment block that defines:\n"
            "  * METRIC NAME: the exact key printed as `{metric}: <value>`\n"
            "  * DIRECTION: whether higher is better or lower is better\n"
            "  * UNITS/SCALE: what the number represents (e.g., MSE in log scale, "
            "accuracy 0-1, discovery rate per episode)\n"
            "  * FORMULA: how the metric is computed from raw experiment outputs\n"
            "  * AGGREGATION: how per-step/per-episode values are reduced to a scalar\n"
            "- Print this definition at runtime: `METRIC_DEF: {metric} | direction=<higher/lower> "
            "| desc=<one-line description>`\n"
            "- Without this definition, the metric is UNINTERPRETABLE and the paper cannot "
            "make any claims about which method is better.\n\n"
            "STATISTICAL RIGOR REQUIREMENT:\n"
            "- Run each condition with at least 5 different random seeds (10+ preferred "
            "if time budget allows). Minimum 3 seeds is MANDATORY.\n"
            "- Print per-seed results: `condition=<name> seed=<s> {metric}: <value>`\n"
            "- Print mean and std across seeds: "
            "`condition=<name> {metric}_mean: <val> {metric}_std: <val>`\n"
            "- If time budget is tight, reduce per-seed iterations rather than "
            "reducing seed count. Minimum 3 seeds is non-negotiable.\n"
            "- ADAPTIVE SEED COUNT: After running a pilot (1 seed, 1 condition), "
            "estimate per-condition time. Then compute: max_seeds = "
            "floor(time_budget / (num_conditions * pilot_time)). Use "
            "min(max(max_seeds, 3), 20) as the seed count. Print "
            "`SEED_COUNT: <N> (budget=<T>s, pilot=<P>s, conditions=<C>)`.\n"
            "- Report bootstrap 95%% confidence intervals when n >= 5.\n\n"
            "FAILURE-AWARE REPORTING (CRITICAL for RL/unstable methods):\n"
            "- Track how many seeds succeed vs fail (NaN, divergence, crash) per "
            "condition. Print: `condition=<name> success_rate: <succeeded>/<total>`\n"
            "- Compute UNCONDITIONAL metrics: treat failed seeds as worst-case "
            "(e.g., metric=0 or metric=worst_baseline). Print: "
            "`condition=<name> unconditional_{metric}_mean: <val>`\n"
            "- This prevents survivorship bias where a method looks good only "
            "because failed runs are excluded.\n"
            "- For RL methods, add STABILITY SAFEGUARDS in the code:\n"
            "  * Gradient clipping (max norm 1.0)\n"
            "  * Reward normalization/clipping to [-10, 10]\n"
            "  * NaN checks on loss/gradients with graceful early stop (not crash)\n"
            "  * Learning rate warmup or conservative initial learning rate\n"
            "  These safeguards should PREVENT most NaN/divergence, not just catch "
            "them after the fact.\n\n"
            "PYTORCH RL IMPLEMENTATION BUGS (CRITICAL — these cause 100%% crash rate):\n"
            "- 'Trying to backward through the graph a second time' is the #1 crash.\n"
            "  CAUSE: reusing a computed tensor across multiple backward() calls.\n"
            "  FIX: Always .detach() values used in the next iteration:\n"
            "  ```\n"
            "  # WRONG:\n"
            "  old_log_prob = policy.log_prob(action)  # still attached to graph\n"
            "  # ... later in update loop:\n"
            "  ratio = new_log_prob / old_log_prob  # backward crashes\n"
            "  \n"
            "  # CORRECT:\n"
            "  old_log_prob = policy.log_prob(action).detach()  # detach!\n"
            "  # ... later in update loop:\n"
            "  ratio = new_log_prob / old_log_prob.detach()  # safe\n"
            "  ```\n"
            "- For PPO: old_log_probs MUST be .detach()ed when stored for later ratio computation.\n"
            "- For value functions: target values MUST be .detach()ed (don't backprop through targets).\n"
            "- For curiosity/intrinsic reward: prediction errors used as reward MUST be .detach()ed.\n"
            "- General rule: any tensor from a PREVIOUS forward pass that is used in the CURRENT "
            "loss computation MUST be .detach()ed.\n"
            "- When in doubt, add .detach() — it never causes crashes, but missing it always does.\n\n"
            "NEURAL NETWORK DIMENSION CONSISTENCY (CRITICAL — #2 crash cause):\n"
            "- 'input and weight.T shapes cannot be multiplied' means obs_dim != network input_dim.\n"
            "- When the environment observation size VARIES across regimes (e.g., easy=6, hard=8), "
            "the neural network's input layer MUST match EACH regime's obs_dim.\n"
            "- FIX: Create the network INSIDE the per-regime loop, or parameterize input_dim:\n"
            "  ```\n"
            "  # WRONG: fixed input_dim for all regimes\n"
            "  policy = PolicyNet(input_dim=10)  # breaks if obs_dim != 10\n"
            "  for regime in regimes:\n"
            "      obs = env.reset()  # obs.shape may vary!\n"
            "  \n"
            "  # CORRECT: dynamic input_dim per regime\n"
            "  for regime in regimes:\n"
            "      obs = env.reset()\n"
            "      obs_dim = obs.shape[-1]  # or len(obs)\n"
            "      policy = PolicyNet(input_dim=obs_dim)  # fresh network per regime\n"
            "  ```\n"
            "- ALWAYS initialize neural networks AFTER knowing the observation dimension.\n\n"
            "PAIRED STATISTICAL ANALYSIS (CRITICAL for publishable results):\n"
            "- Use the SAME random seeds across all conditions so results are paired.\n"
            "- After collecting per-seed results for all conditions, compute paired "
            "differences: for each seed s, diff(s) = method(s) - baseline(s).\n"
            "- Print paired analysis: "
            "`PAIRED: <method> vs <baseline> mean_diff=<val> std_diff=<val> "
            "t_stat=<val> p_value=<val>`\n"
            "- Also print bootstrap 95%% CI of the paired difference.\n"
            "- This is FAR more powerful than independent comparisons because it "
            "controls for seed-to-seed variance.\n\n"
            "MULTI-REGIME REQUIREMENT (CRITICAL for generality claims):\n"
            "- The experiment MUST test at least 2 different difficulty/noise regimes "
            "(e.g., low noise vs high noise, small hypothesis space vs large).\n"
            "- Report results per-regime, not just aggregated across regimes.\n"
            "- Print regime labels: "
            "`condition=<name> regime=<regime_name> {metric}: <value>`\n"
            "- This prevents conclusions that only hold in one setting from being "
            "presented as general findings.\n\n"
            "DIMENSION CONSISTENCY CHECK (CRITICAL for RL/neural methods):\n"
            "- Before passing observations/states to neural networks or policy "
            "parameters, VERIFY that dimensions match. Common bug: environment "
            "state has dimension D1 but network expects D2.\n"
            "- At the start of each condition, print the state/observation "
            "dimension and the network input dimension. If they mismatch, "
            "reshape or adjust the network before proceeding.\n"
            "- Test EVERY condition with a single dry-run step before the full "
            "loop to catch shape mismatches early.\n\n"
            "TIME-TO-EVENT METRIC BUG PREVENTION (CRITICAL — common silent bug):\n"
            "- If the primary metric is a 'time-to-X' measure (e.g., time-to-discovery, "
            "steps-to-convergence, episodes-to-threshold), you MUST check the success "
            "criterion at EVERY step inside the loop, not only at the end.\n"
            "- WRONG pattern (produces degenerate ceiling data):\n"
            "  ```\n"
            "  for t in range(horizon):\n"
            "      obs, r, done, info = env.step(a)\n"
            "  success = check(info)  # only checked ONCE at end\n"
            "  time_to_X = horizon if not success else t + 1  # t+1 = horizon always!\n"
            "  ```\n"
            "- CORRECT pattern (captures actual first-success time):\n"
            "  ```\n"
            "  time_to_X = horizon  # default: never succeeded\n"
            "  for t in range(horizon):\n"
            "      obs, r, done, info = env.step(a)\n"
            "      if check(info) and time_to_X == horizon:  # first success\n"
            "          time_to_X = t + 1\n"
            "      if done: break\n"
            "  ```\n"
            "- This bug causes ALL methods to return the same ceiling value, making "
            "the entire experiment useless. Every method looks identical at the cap.\n"
            "- APPLY THIS TO ALL CONDITIONS: RandomSearch, BO, RL — every single "
            "condition must check at every step. If even one condition uses the wrong "
            "pattern, the comparison is invalid.\n\n"
            "METRIC DISCRIMINATION VALIDATION (CRITICAL):\n"
            "- After running all conditions, check if all conditions produce the SAME "
            "mean metric value. If they do, the metric is NOT discriminative and the "
            "experiment is scientifically useless.\n"
            "- Common causes: ceiling/floor effects, too-easy or too-hard tasks, "
            "time-to-event bug above, metric that doesn't capture real differences.\n"
            "- If all conditions have identical means, print "
            "`WARNING: DEGENERATE_METRICS all conditions have same mean=<val>` "
            "and you MUST take corrective action:\n"
            "  (a) If all means = 1.0 or max: increase task difficulty (reduce budget, "
            "increase noise, enlarge hypothesis space)\n"
            "  (b) If all means = 0.0: decrease difficulty\n"
            "  (c) Re-run after adjustment and verify means now differ\n"
            "  (d) If adjustments don't help, switch to a different primary metric\n"
            "- A degenerate experiment CANNOT produce a publishable paper. Fix it.\n\n"
            "DIFFICULTY CALIBRATION (CRITICAL for meaningful results):\n"
            "- After running a pilot (3-5 seeds, 2 conditions: random_search + one RL), "
            "check BOTH success rate AND metric discrimination.\n"
            "- TWO things must be true for the experiment to be informative:\n"
            "  1. Success rate between 30-80%% (not too hard, not too easy)\n"
            "  2. Primary metric varies across conditions (not all methods score the same)\n"
            "- CEILING DETECTION (CRITICAL): If primary_metric is 1.0 (or max possible) "
            "for ALL pilot seeds in ALL pilot conditions, the task is TRIVIALLY EASY. "
            "You MUST increase difficulty until the metric varies. Options:\n"
            "  * Reduce experiment budget/horizon (fewer steps to find solution)\n"
            "  * Increase hypothesis space size\n"
            "  * Increase observation noise\n"
            "  * Tighten the success criterion (e.g., require closer match)\n"
            "  * Reduce the number of allowed experiments per episode\n"
            "- FLOOR DETECTION: If primary_metric is 0.0 for all conditions, task is "
            "too hard. Reduce noise, enlarge budget, simplify.\n"
            "- Print `CALIBRATION: regime=<name> pilot_success_rate=<val> "
            "pilot_primary_metric_std=<val>` after calibration.\n"
            "- If std=0, the metric is NOT discriminative — adjust until std > 0.\n"
            "- Run a calibration loop: pilot → check → adjust → re-pilot (max 3 iterations).\n\n"
            "ALGORITHM IMPLEMENTATION INTEGRITY (CRITICAL — mismatch = academic fraud):\n"
            "1. If you name a method 'Bayesian Optimization', you MUST implement:\n"
            "   - A surrogate model (e.g., Gaussian Process or random forest)\n"
            "   - An acquisition function (e.g., Expected Improvement, UCB)\n"
            "   - Surrogate model updates after each observation\n"
            "   DO NOT implement UCB1 bandit and call it 'Bayesian Optimization'.\n"
            "2. If you name a method 'PPO', you MUST implement:\n"
            "   - A clipped surrogate objective: min(r_t * A_t, clip(r_t, 1-eps, 1+eps) * A_t)\n"
            "   - A learned value function baseline\n"
            "   - The clip_eps parameter MUST be used in the policy update\n"
            "   DO NOT implement vanilla REINFORCE and call it 'PPO'.\n"
            "3. Every declared hyperparameter MUST be used in the algorithm:\n"
            "   - If you declare clip_eps, it must appear in the loss computation\n"
            "   - If you declare entropy_coef, it must be added to the policy loss\n"
            "   - Dead parameters (declared but never used) are strictly forbidden\n"
            "4. Ablation conditions MUST produce different behavior:\n"
            "   - Two conditions that differ only in a parameter that is never read are IDENTICAL\n"
            "   - Verify: if two conditions produce identical outputs on the same seed, "
            "the ablation is broken and MUST be fixed\n"
            "   ABLATION DESIGN PATTERN (CRITICAL — #1 cause of broken ablations):\n"
            "   - 'no_key_component': Must REMOVE a core algorithmic component "
            "(e.g., disable the graph structure by zeroing the adjacency, or remove "
            "the contrastive loss, or disable the RL policy and use random actions). "
            "The removal MUST change the forward() / step() computation.\n"
            "   - 'reduced_capacity': Must REDUCE model capacity by at least 2x "
            "(e.g., halve hidden dimensions, reduce layers, shrink embedding size). "
            "This MUST create a new model with different architecture, NOT just "
            "rename a parameter with the same value.\n"
            "   - SELF-TEST: After implementing ablations, add a startup check that "
            "runs one forward pass per condition on the SAME input and asserts outputs "
            "differ. Print: `ABLATION_CHECK: <name1> vs <name2> outputs_differ=True`.\n"
            "   - If outputs are identical, the ablation is BROKEN — do not proceed.\n\n"
            "MINIMUM SEED COUNT (CRITICAL — 3 seeds = unpublishable):\n"
            "- Use AT LEAST 5 random seeds per condition (10 preferred if time permits)\n"
            "- Use AT LEAST 30 episodes per seed for RL methods\n"
            "- When computing bootstrap CIs, use at least 1000 bootstrap samples\n"
            "- For method comparisons: use paired bootstrap or Wilcoxon signed-rank test\n"
            "- Report effect sizes (Cohen's d) alongside p-values\n\n"
            "Experiment plan:\n{exp_plan}"
        ),
        "max_tokens": 8192,
    },
    "resource_planning": {
        "system": "You are an experiment scheduler.",
        "user": (
            "Create schedule JSON with GPU/time estimates.\n"
            "Schema: {tasks:[{id,name,depends_on,gpu_count,estimated_minutes,"
            "priority}], total_gpu_budget, generated}.\n"
            "Experiment plan:\n{exp_plan}"
        ),
        "json_mode": True,
    },
    # ── Phase F: Analysis & Decision ─────────────────────────────────────
    "result_analysis": {
        "system": (
            "You are a quantitative ML analyst. Always cite exact numbers "
            "from the provided data."
        ),
        "user": (
            "{preamble}\n\n"
            "{data_context}\n\n"
            "Analyze run metrics and produce markdown report with statistical "
            "interpretation.\n"
            "Use the ACTUAL quantitative values provided above — do NOT invent "
            "numbers.\n\n"
            "SANITY CHECKS (perform BEFORE interpreting results):\n"
            "1. MONOTONICITY: If a condition scales a parameter (e.g., N agents, "
            "model size), check whether metrics move in the expected direction. "
            "If accuracy *decreases* when adding more agents under majority voting, "
            "flag this as a likely implementation bug (vote parsing, normalization, "
            "or aggregation issue).\n"
            "2. BASELINE PLAUSIBILITY: Random-chance baselines should match "
            "theoretical expectations (e.g., 1/K for K-class classification).\n"
            "3. CROSS-CONDITION CONSISTENCY: Results across datasets or conditions "
            "should be internally coherent — wildly different patterns may indicate "
            "confounds or bugs.\n"
            "4. REPLICATION: If results are from a single seed (n=1), explicitly "
            "note that no statistical significance claims can be made.\n"
            "5. ABLATION ISOLATION: Compare per-seed values across conditions. If "
            "two conditions produce IDENTICAL values for the same seed, this is a "
            "RED FLAG — the ablation/variant may not have actually changed the code "
            "path (e.g., config not applied, caching, shared state). Flag this "
            "explicitly and recommend a config/registry audit.\n"
            "6. METRIC DEFINITION CHECK: Look for a `METRIC_DEF:` line in the output. "
            "If absent, flag that the primary metric is UNDEFINED — direction, units, "
            "and formula are unknown, making all comparisons uninterpretable. This is "
            "a critical methodology gap.\n"
            "7. CONDITION COMPLETENESS CHECK: Look for `REGISTERED_CONDITIONS:` in "
            "the output. Compare against the experiment plan. If conditions are missing "
            "or failed (look for `CONDITION_FAILED:`), list them explicitly and assess "
            "whether the remaining conditions can still answer the research question.\n"
            "8. DEGENERATE METRICS CHECK: If ALL conditions (or all but one) produce "
            "the SAME mean primary metric value, flag this as DEGENERATE — the metric "
            "is NOT discriminative. Common causes: (a) time-to-event metric that only "
            "checks success at the final step (returns horizon for all methods), "
            "(b) ceiling/floor effects from too-easy or too-hard tasks, "
            "(c) metric capped at a budget value. This makes the experiment "
            "scientifically useless — recommend REFINE with a note to fix the metric "
            "computation or task difficulty. Look for `WARNING: DEGENERATE_METRICS` "
            "in stdout. Even if not printed, check the numbers yourself.\n\n"
            "Required sections: Metrics Summary (with real values), "
            "Consensus Findings (high confidence), "
            "Contested Points (with evidence-based resolution), "
            "Statistical Checks, Methodology Audit, Limitations, Conclusion.\n"
            "In the Conclusion, include:\n"
            "- Result quality rating (1-10)\n"
            "- Key findings (3-5)\n"
            "- Methodology gaps to address next\n"
            "- Recommendation: PROCEED / REFINE / PIVOT\n\n"
            "Run context:\n{context}"
        ),
        "max_tokens": 8192,
    },
    "research_decision": {
        "system": "You are a research program lead making go/no-go decisions.",
        "user": (
            "Based on the analysis, make one of three decisions:\n"
            "- **PROCEED** — results are sufficient, move to paper writing\n"
            "- **PIVOT** — hypotheses are fundamentally flawed, generate new ones\n"
            "- **REFINE** — hypotheses are sound but experiments need re-tuning\n\n"
            "MINIMUM QUALITY CRITERIA for PROCEED (ALL must be met):\n"
            "1. At least 2 baselines AND the proposed method have results\n"
            "2. The primary metric is defined (direction, units known)\n"
            "3. Each condition has results from ≥3 seeds\n"
            "4. No identical per-seed values across different conditions (ablation integrity)\n"
            "5. The analysis quality rating is ≥4/10\n"
            "If ANY criterion is not met, you MUST choose REFINE (not PROCEED).\n\n"
            "Output markdown with sections:\n"
            "## Decision\n"
            "State exactly one of: PROCEED, PIVOT, or REFINE\n\n"
            "## Justification\n"
            "Why this decision is warranted based on evidence.\n\n"
            "## Evidence\n"
            "Key data points supporting the decision.\n\n"
            "## Next Actions\n"
            "Concrete steps for the chosen path.\n\n"
            "Analysis:\n{analysis}"
        ),
    },
    # ── Phase G: Paper Writing ───────────────────────────────────────────
    "paper_outline": {
        "system": "You are an academic writing planner.",
        "user": (
            "{preamble}\n\n"
            "Create a detailed paper outline in markdown.\n"
            "Include per-section goals and evidence links.\n"
            "{topic_constraint}"
            "{feedback}"
            "Analysis:\n{analysis}\n\nDecision:\n{decision}"
        ),
        "max_tokens": 8192,
    },
    "paper_draft": {
        "system": (
            "You are a top-tier ML paper author writing for NeurIPS/ICML/ICLR.\n\n"
            "KEY PRINCIPLES (from accepted paper analyses):\n"
            "1. NOVELTY: A good paper has 1-2 key ideas and keeps the rest simple.\n"
            "2. NARRATIVE: A short, rigorous, evidence-based technical story with a takeaway.\n"
            "3. STRONG BASELINES: Invest real effort in making baselines competitive.\n"
            "4. ABLATIONS: Remove one component at a time and measure the effect.\n"
            "5. HONESTY: Acknowledge limitations explicitly.\n"
            "6. REPRODUCIBILITY: Include all details needed to reproduce results.\n\n"
            "EVIDENCE-BOUNDING RULES (CRITICAL — violation = reject):\n"
            "7. EVERY claim in the title, abstract, and conclusion MUST be directly "
            "supported by specific experimental metrics provided below.\n"
            "8. If the experiment only covers partial conditions, the title MUST NOT "
            "make global causal claims. Use 'Toward...', 'Investigating...', or "
            "'An Empirical Study of...' instead of 'X Dominates Y'.\n"
            "9. BEFORE writing the title, list the conditions actually tested and "
            "their metric values. The title must only claim what those numbers show.\n"
            "10. If a metric is a single scalar without condition labels, do NOT "
            "claim comparative results between strategies/methods.\n"
            "11. Distinguish between 'we propose and validate' (has full results) vs "
            "'we propose and present preliminary evidence' (partial results).\n\n"
            "You ONLY use real experimental data — never fabricate or approximate numbers.\n\n"
            "METHOD SECTION REQUIREMENTS:\n"
            "12. The Method section MUST include ALL implementation details needed "
            "for reproduction: algorithm pseudocode or step-by-step description, "
            "hyperparameters (learning rate, clipping, discount factor, etc.), "
            "state/observation representation, reward definition, and baseline "
            "configurations.\n"
            "13. For RL methods: specify policy architecture, training procedure "
            "(number of rollouts, epochs, batch handling), and any stability "
            "mechanisms (gradient clipping, reward normalization).\n"
            "14. For baselines: specify acquisition function, surrogate model, "
            "and any tuning performed to make baselines competitive.\n\n"
            "FAILURE-AWARE REPORTING REQUIREMENTS:\n"
            "15. If any method has a success rate < 100%%, the Results section "
            "MUST report success rates per method and explain inclusion/exclusion "
            "criteria.\n"
            "16. Report BOTH conditional metrics (successful runs only) AND "
            "unconditional metrics (treating failures as worst-case). Without "
            "both, comparative claims are biased by survivorship.\n"
            "17. The Limitations section MUST discuss stability/reliability "
            "if any method showed NaN/divergence/crashes.\n\n"
            "BENCHMARK & ENVIRONMENT SPECIFICATION:\n"
            "18. The Experiments section MUST fully specify the evaluation "
            "environment: state/observation space, action space, hypothesis space, "
            "noise model, episode length, and any randomization procedures.\n"
            "19. Report results PER REGIME (e.g., per noise level, per problem "
            "size) with separate tables or sub-sections. Aggregated-only results "
            "cannot support claims about robustness or generality.\n"
            "20. Include a table comparing all methods across all regimes with "
            "paired statistical tests (bootstrap CI of paired differences, or "
            "paired t-test p-values). Without this, comparative claims lack "
            "statistical grounding.\n\n"
            "METHOD NAMING RULES:\n"
            "21. NEVER use generic labels like 'baseline_1', 'method_variant_1', "
            "'method_variant_2' in the paper. Use descriptive algorithm names "
            "(e.g., 'Random Search', 'Bayesian Optimization', 'PPO', "
            "'Curiosity-Driven RL'). Generic labels make the paper "
            "scientifically uninterpretable.\n"
            "22. Each method MUST have a full description: architecture, "
            "training procedure, key hyperparameters, and implementation details. "
            "A reader should be able to reimplement every method from the paper alone.\n\n"
            "STATISTICAL REPORTING (MANDATORY for acceptance):\n"
            "23. EVERY result table MUST include 95%% confidence intervals "
            "(mean +/- CI or [low, high]).\n"
            "24. EVERY comparison claim ('A outperforms B') MUST cite p-value. "
            "If p >= 0.05, write: 'The difference is not statistically significant.'\n"
            "25. If the proposed method does NOT statistically significantly "
            "outperform a baseline, do NOT claim superiority. Reframe as "
            "'comparable', 'competitive', or 'negative result'.\n\n"
            "WRITING STYLE RULES:\n"
            "26. DO NOT repeat disclaimers like 'due to computational constraints, "
            "this analysis was not conducted' more than once. State each limitation "
            "ONCE in the Limitations section.\n"
            "27. The Limitations section should be concise (200-400 words) listing "
            "3-5 key limitations. Do NOT scatter limitation disclaimers throughout "
            "every section.\n"
            "28. Focus 80%% of the paper on WHAT YOU DID and WHAT YOU FOUND, not "
            "on what you could not do. Positive scientific contribution should "
            "dominate the paper.\n"
            "29. Cite 25-40 unique references in the paper body. The Related Work "
            "section alone should cite at least 15 references. Cite only directly "
            "relevant work — do NOT pad with tangentially related papers.\n"
            "30. CITE ORIGINAL PAPERS: When discussing a technique (e.g., Batch "
            "Normalization, ResNet, Adam, PPO), ALWAYS cite the original paper that "
            "introduced it. Do NOT cite a survey or follow-up instead of the original. "
            "The available references list includes foundational papers — use them.\n"
            "31. BASELINE MODERNITY: When discussing baselines and comparisons, ensure "
            "the paper acknowledges whether the baselines represent current practice. "
            "If baselines are older methods, explicitly discuss why they were chosen "
            "and acknowledge stronger modern alternatives exist."
        ),
        "user": (
            "{preamble}\n\n"
            "Write a full paper draft section by section in markdown.\n"
            "Required sections: Title, Abstract, Introduction, Related Work, "
            "Method, Experiments, Results, Limitations, Conclusion, "
            "References.\n"
            "{writing_structure}\n"
            "{topic_constraint}"
            "{exp_metrics_instruction}"
            "{citation_instruction}"
            "Outline:\n{outline}"
        ),
        "max_tokens": 16384,
    },
    "peer_review": {
        "system": "You are a balanced conference reviewer.",
        "user": (
            "Simulate peer review from at least 3 reviewer perspectives.\n"
            "Output markdown with Reviewer A (methodology expert), "
            "Reviewer B (domain expert), and Reviewer C (statistics/rigor expert), "
            "each including strengths, weaknesses, and actionable revisions.\n\n"
            "Check specifically:\n"
            "1. TOPIC ALIGNMENT: Does the paper stay on topic ({topic})? "
            "Flag any sections where the paper drifts to unrelated topics or "
            "presents environment issues as contributions.\n"
            "2. CLAIM-EVIDENCE ALIGNMENT: For EACH claim in the title, abstract, "
            "and conclusion, verify there is a specific metric/table/figure in "
            "the Results section supporting it. Flag unsupported claims.\n"
            "3. STATISTICAL VALIDITY: Are confidence intervals or error bars "
            "reported? Is n>1 (multiple seeds)? Are significance tests appropriate?\n"
            "4. COMPLETENESS: Does the paper have all required sections with "
            "sufficient depth? A NeurIPS paper body should be 5,000-6,500 words.\n"
            "5. REPRODUCIBILITY: Are hyperparameters, random seeds, compute "
            "resources, and dataset details fully specified?\n\n"
            "Paper draft:\n{draft}"
        ),
        "max_tokens": 8192,
    },
    "paper_revision": {
        "system": (
            "You are a paper revision expert.\n\n"
            "TITLE AND ABSTRACT ALIGNMENT (CRITICAL):\n"
            "- After reviewing experimental evidence, UPDATE the title if results "
            "do not support the original claim.\n"
            "- If the proposed method does NOT beat baselines, use a title like "
            "'An Empirical Study of...', 'When X Falls Short: ...', or "
            "'Investigating ... : Negative Results and Insights'.\n"
            "- Rewrite the abstract to accurately reflect what was FOUND, not "
            "what was hoped. The abstract must match actual numbers.\n"
            "- The conclusion MUST match actual results — no aspirational claims.\n\n"
            "IMPORTANT WRITING RULES:\n"
            "- Do NOT add disclaimers like 'due to computational constraints' "
            "or 'this analysis was not conducted'. If a limitation exists, "
            "mention it ONCE in the Limitations section only.\n"
            "- Focus 80%% of the paper on what was DONE and what was FOUND.\n"
            "- Do NOT add hedging language that was not in the original draft.\n"
            "- Keep Limitations to 200-400 words with 3-5 concise points.\n"
            "- Ensure every comparison claim cites a p-value or states that "
            "the difference is not statistically significant.\n"
        ),
        "user": (
            "Revise the paper draft to address all review comments.\n"
            "Return revised markdown only.\n"
            "{writing_structure}\n"
            "{topic_constraint}"
            "Draft:\n{draft}\n\nReviews:\n{reviews}"
        ),
        "max_tokens": 16384,
    },
    # ── Phase H: Finalization ────────────────────────────────────────────
    "quality_gate": {
        "system": "You are a final quality gate evaluator.",
        "user": (
            "Evaluate revised paper quality and return JSON.\n"
            "Schema: {score_1_to_10:number, verdict:string, strengths:[...], "
            "weaknesses:[...], required_actions:[...]}.\n"
            "Threshold: {quality_threshold}\n"
            "Paper:\n{revised}"
        ),
        "json_mode": True,
    },
    "knowledge_archive": {
        "system": "You produce reproducibility-focused research retrospectives.",
        "user": (
            "{preamble}\n\n"
            "Write retrospective archive markdown with lessons, "
            "reproducibility notes, and future work.\n"
            "Decision:\n{decision}\n\nAnalysis:\n{analysis}\n\n"
            "Revised paper:\n{revised}"
        ),
        "max_tokens": 8192,
    },
    "export_publish": {
        "system": "You are a publication formatting editor.",
        "user": (
            "Format revised paper into clean final markdown for publication "
            "export.\n"
            "Preserve content quality and readability.\n"
            "Input paper:\n{revised}"
        ),
        "max_tokens": 16384,
    },
}

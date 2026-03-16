#!/usr/bin/env python3
"""
agent_runner.py — Unified LLM/agent caller for AutoResearchClaw v2.6 "Agent Mode"

Provides a single run_prompt() function that ALL scripts import instead of
calling anthropic.Anthropic() directly. Detects `provider` from config.yaml
and routes accordingly.

Supported providers:
  anthropic   → Anthropic-compatible API (default; also handles minimax/Kimi)
  minimax     → Alias for anthropic (same Anthropic-compat endpoint)
  openai      → OpenAI API via openai.OpenAI().chat.completions.create()
  codex       → Codex CLI subprocess: codex --print --model <model> <prompt>
  claude-code → Claude Code CLI subprocess: claude --print --permission-mode bypassPermissions <prompt>

Usage:
    from agent_runner import run_prompt
    result = run_prompt(system_prompt, user_message)

    # Override config at call-time (optional):
    result = run_prompt(system_prompt, user_message, config=my_cfg)
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import yaml

# ── Config loader ──────────────────────────────────────────────────────────────

_DEFAULT_CFG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"


def _load_config(cfg_path: Optional[Path] = None) -> dict:
    path = cfg_path or _DEFAULT_CFG_PATH
    if path.exists():
        return yaml.safe_load(path.read_text()) or {}
    return {}


# ── Core dispatcher ────────────────────────────────────────────────────────────

def run_prompt(
    system_prompt: str,
    user_message: str,
    max_tokens: int = 2048,
    config: Optional[dict] = None,
) -> str:
    """
    Call the configured LLM/agent and return the response text.

    Args:
        system_prompt: System-level instructions for the model.
        user_message:  The user turn content.
        max_tokens:    Max tokens to generate (ignored by CLI providers).
        config:        Override config dict. If None, loads from config.yaml.

    Returns:
        Response text as a string. Never raises — returns an error string on failure.
    """
    cfg = config if config is not None else _load_config()
    llm_cfg = cfg.get("llm", {})

    provider = llm_cfg.get("provider", "anthropic").lower()
    model = llm_cfg.get("model", "claude-3-5-haiku-20241022")
    api_key = llm_cfg.get("api_key") or os.getenv("ANTHROPIC_API_KEY", "")
    base_url = llm_cfg.get("base_url") or None

    if provider in ("anthropic", "minimax"):
        return _run_anthropic(system_prompt, user_message, max_tokens, model, api_key, base_url)
    elif provider == "openai":
        openai_key = api_key or os.getenv("OPENAI_API_KEY", "")
        return _run_openai(system_prompt, user_message, max_tokens, model, openai_key, base_url)
    elif provider == "codex":
        return _run_codex(system_prompt, user_message, model)
    elif provider == "claude-code":
        return _run_claude_code(system_prompt, user_message)
    else:
        # Unknown provider — fall back to anthropic
        print(f"[agent_runner] Unknown provider '{provider}', falling back to anthropic")
        return _run_anthropic(system_prompt, user_message, max_tokens, model, api_key, base_url)


# ── Provider implementations ───────────────────────────────────────────────────

def _run_anthropic(
    system_prompt: str,
    user_message: str,
    max_tokens: int,
    model: str,
    api_key: str,
    base_url: Optional[str],
) -> str:
    """Anthropic-compatible API (handles Anthropic Claude and MiniMax Kimi)."""
    try:
        import anthropic
    except ImportError:
        return "[agent_runner] ERROR: 'anthropic' package not installed. Run: pip install anthropic"

    if not api_key:
        return "[agent_runner] ERROR: No API key configured (llm.api_key or ANTHROPIC_API_KEY)"

    try:
        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = anthropic.Anthropic(**client_kwargs)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        # Handle ThinkingBlock (MiniMax/Kimi) — grab the first TextBlock
        text = next((b.text for b in response.content if hasattr(b, "text")), "")
        return text
    except Exception as e:
        return f"[agent_runner] Anthropic API error: {str(e)[:300]}"


def _run_openai(
    system_prompt: str,
    user_message: str,
    max_tokens: int,
    model: str,
    api_key: str,
    base_url: Optional[str],
) -> str:
    """OpenAI API via openai.OpenAI().chat.completions.create()."""
    try:
        from openai import OpenAI
    except ImportError:
        return "[agent_runner] ERROR: 'openai' package not installed. Run: pip install openai"

    if not api_key:
        return "[agent_runner] ERROR: No API key configured (llm.api_key or OPENAI_API_KEY)"

    try:
        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(**client_kwargs)
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        return f"[agent_runner] OpenAI API error: {str(e)[:300]}"


def _run_codex(system_prompt: str, user_message: str, model: str) -> str:
    """
    Codex CLI agent via subprocess.
    Combines system + user into one prompt passed as CLI argument.
    """
    combined_prompt = f"{system_prompt}\n\n---\n\n{user_message}"
    cmd = ["codex", "--print", "--model", model, combined_prompt]
    return _run_cli_subprocess(cmd, binary_name="codex")


def _run_claude_code(system_prompt: str, user_message: str) -> str:
    """
    Claude Code CLI agent via subprocess.
    Combines system + user into one prompt passed as CLI argument.
    """
    combined_prompt = f"{system_prompt}\n\n---\n\n{user_message}"
    cmd = [
        "claude",
        "--print",
        "--permission-mode", "bypassPermissions",
        combined_prompt,
    ]
    return _run_cli_subprocess(cmd, binary_name="claude (Claude Code)")


def _run_cli_subprocess(cmd: list, binary_name: str) -> str:
    """Run a CLI agent subprocess and capture stdout."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute hard cap
        )
        if result.returncode != 0:
            stderr_snippet = (result.stderr or "")[:300]
            return (
                f"[agent_runner] {binary_name} exited with code {result.returncode}. "
                f"stderr: {stderr_snippet}"
            )
        output = result.stdout.strip()
        return output if output else f"[agent_runner] {binary_name} returned empty output."
    except FileNotFoundError:
        return (
            f"[agent_runner] ERROR: '{cmd[0]}' binary not found. "
            f"Install {binary_name} and make sure it's on your PATH."
        )
    except subprocess.TimeoutExpired:
        return f"[agent_runner] ERROR: {binary_name} timed out after 300s."
    except Exception as e:
        return f"[agent_runner] {binary_name} subprocess error: {str(e)[:300]}"


# ── Provider info helper (for /provider bot command) ──────────────────────────

def get_provider_info(config: Optional[dict] = None) -> dict:
    """
    Return a dict with current provider info for display purposes.
    {
        "provider": str,
        "model": str,
        "base_url": str | None,
        "agent_mode": bool,  # True if codex or claude-code
    }
    """
    cfg = config if config is not None else _load_config()
    llm_cfg = cfg.get("llm", {})
    provider = llm_cfg.get("provider", "anthropic")
    return {
        "provider": provider,
        "model": llm_cfg.get("model", "—"),
        "base_url": llm_cfg.get("base_url") or None,
        "agent_mode": provider.lower() in ("codex", "claude-code"),
    }


# ── CLI self-test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test agent_runner with a simple prompt")
    parser.add_argument("--system", default="You are a helpful assistant.")
    parser.add_argument("--user", default="Say hello in one sentence.")
    parser.add_argument("--max-tokens", type=int, default=100)
    args = parser.parse_args()

    info = get_provider_info()
    print(f"[agent_runner] Provider: {info['provider']} | Model: {info['model']} | Agent Mode: {info['agent_mode']}")
    print(f"[agent_runner] Running prompt...")

    result = run_prompt(args.system, args.user, max_tokens=args.max_tokens)
    print(f"\nResponse:\n{result}")

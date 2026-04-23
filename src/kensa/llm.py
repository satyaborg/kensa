"""Provider-agnostic LLM completion client.

Mirrors ``judge.py``'s provider resolution, but exposes a free-form
``complete(prompt)`` instead of a pass/fail judge verdict. Used by
``kensa generate`` and any future caller that needs a one-shot LLM call.
"""

from __future__ import annotations

import os
from typing import Protocol


class Completer(Protocol):
    """Protocol for LLM completion providers."""

    def complete(self, prompt: str, *, response_format: str | None = None) -> str: ...


class AnthropicCompleter:
    """Completion via Anthropic's Claude API."""

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        try:
            import anthropic
        except ImportError as err:
            from kensa.utils import install_hint

            raise ImportError(
                "anthropic package required for Anthropic completions. "
                f"Install with: {install_hint('anthropic')}"
            ) from err
        self.client = anthropic.Anthropic()
        self.model = model

    def complete(self, prompt: str, *, response_format: str | None = None) -> str:
        system = (
            "Respond with only a single JSON object. No prose, no markdown fences."
            if response_format == "json"
            else None
        )
        if system is None:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
        else:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        for block in response.content:
            if hasattr(block, "text"):
                return str(block.text)
        return ""


class OpenAICompleter:
    """Completion via OpenAI's API."""

    def __init__(self, model: str = "gpt-5.4-mini") -> None:
        try:
            import openai
        except ImportError as err:
            from kensa.utils import install_hint

            raise ImportError(
                "openai package required for OpenAI completions. "
                f"Install with: {install_hint('openai')}"
            ) from err
        self.client = openai.OpenAI()
        self.model = model

    def complete(self, prompt: str, *, response_format: str | None = None) -> str:
        if response_format == "json":
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                response_format={"type": "json_object"},
            )
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
            )
        return response.choices[0].message.content or ""


def get_completer(model: str | None = None) -> Completer:
    """Resolve which completion provider to use.

    Priority: KENSA_JUDGE_MODEL env var → ANTHROPIC_API_KEY → OPENAI_API_KEY → error.
    Mirrors ``judge.get_judge`` so a single API key unlocks both judging and generation.
    """
    from kensa.runner import ensure_dotenv_loaded

    ensure_dotenv_loaded()

    model_override = model or os.environ.get("KENSA_JUDGE_MODEL")

    if model_override:
        if "claude" in model_override or "anthropic" in model_override.lower():
            return AnthropicCompleter(model=model_override)
        return OpenAICompleter(model=model_override)

    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicCompleter()

    if os.environ.get("OPENAI_API_KEY"):
        return OpenAICompleter()

    raise RuntimeError(
        "No LLM provider available. Set one of:\n"
        "  KENSA_JUDGE_MODEL=<model>  (explicit model)\n"
        "  ANTHROPIC_API_KEY=<key>     (uses claude-sonnet-4-6)\n"
        "  OPENAI_API_KEY=<key>        (uses gpt-5.4-mini)\n"
        "Keys can be in a .env file (searched up from cwd) or exported."
    )

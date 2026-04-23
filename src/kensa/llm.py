"""Provider-agnostic LLM client helpers.

Shared plumbing for ``judge.py`` and ``generate.py``: client construction with
friendly install hints, the provider priority tree (``KENSA_JUDGE_MODEL`` →
Anthropic key → OpenAI key), and a free-form ``Completer`` protocol for
callers that need a one-shot text completion rather than a judge verdict.
"""

from __future__ import annotations

import os
from typing import Any, Literal, Protocol

Provider = Literal["anthropic", "openai"]


class Completer(Protocol):
    """Protocol for LLM completion providers."""

    def complete(self, prompt: str, *, response_format: str | None = None) -> str: ...


def _anthropic_client() -> Any:
    """Construct an Anthropic SDK client, raising a friendly error if missing."""
    try:
        import anthropic
    except ImportError as err:
        from kensa.utils import install_hint

        raise ImportError(
            f"anthropic package required. Install with: {install_hint('anthropic')}"
        ) from err
    return anthropic.Anthropic()


def _openai_client() -> Any:
    """Construct an OpenAI SDK client, raising a friendly error if missing."""
    try:
        import openai
    except ImportError as err:
        from kensa.utils import install_hint

        raise ImportError(
            f"openai package required. Install with: {install_hint('openai')}"
        ) from err
    return openai.OpenAI()


def resolve_provider(model: str | None = None) -> tuple[Provider, str | None]:
    """Return ``(provider, model_override)`` for an LLM call.

    Shared by ``judge.get_judge`` and ``get_completer``. Priority:
    ``KENSA_JUDGE_MODEL`` env var → ``ANTHROPIC_API_KEY`` → ``OPENAI_API_KEY``.
    Loads ``.env`` walking up from cwd so subprocess and parent see the same
    keys. Returns ``None`` for the model override when we're falling back to
    the provider's built-in default.
    """
    from kensa.runner import ensure_dotenv_loaded

    ensure_dotenv_loaded()

    model_override = model or os.environ.get("KENSA_JUDGE_MODEL")

    if model_override:
        if "claude" in model_override or "anthropic" in model_override.lower():
            return ("anthropic", model_override)
        return ("openai", model_override)

    if os.environ.get("ANTHROPIC_API_KEY"):
        return ("anthropic", None)

    if os.environ.get("OPENAI_API_KEY"):
        return ("openai", None)

    raise RuntimeError(
        "No LLM provider available. Set one of:\n"
        "  KENSA_JUDGE_MODEL=<model>  (explicit model)\n"
        "  ANTHROPIC_API_KEY=<key>     (uses claude-sonnet-4-6)\n"
        "  OPENAI_API_KEY=<key>        (uses gpt-5.4-mini)\n"
        "Keys can be in a .env file (searched up from cwd) or exported."
    )


class AnthropicCompleter:
    """Completion via Anthropic's Claude API."""

    def __init__(self, model: str = "claude-sonnet-4-6", *, max_tokens: int = 4096) -> None:
        self.client = _anthropic_client()
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, prompt: str, *, response_format: str | None = None) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if response_format == "json":
            kwargs["system"] = (
                "Respond with only a single JSON object. No prose, no markdown fences."
            )
        response = self.client.messages.create(**kwargs)
        for block in response.content:
            if hasattr(block, "text"):
                return str(block.text)
        return ""


class OpenAICompleter:
    """Completion via OpenAI's API (reasoning-model style: max_completion_tokens)."""

    def __init__(self, model: str = "gpt-5.4-mini", *, max_tokens: int = 4096) -> None:
        self.client = _openai_client()
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, prompt: str, *, response_format: str | None = None) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_completion_tokens": self.max_tokens,
        }
        if response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""


def get_completer(model: str | None = None) -> Completer:
    """Resolve which completion provider to use.

    Priority: ``KENSA_JUDGE_MODEL`` env var → ``ANTHROPIC_API_KEY`` →
    ``OPENAI_API_KEY`` → error. Mirrors ``judge.get_judge`` so a single API
    key unlocks both judging and generation.
    """
    provider, resolved_model = resolve_provider(model)
    if provider == "anthropic":
        return AnthropicCompleter(model=resolved_model) if resolved_model else AnthropicCompleter()
    return OpenAICompleter(model=resolved_model) if resolved_model else OpenAICompleter()

"""Unit tests for kensa.llm — provider resolution and stubbed completers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kensa.llm import (
    AnthropicCompleter,
    OpenAICompleter,
    _anthropic_client,
    _openai_client,
    get_completer,
    resolve_provider,
)


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent ensure_dotenv_loaded from reloading the project's .env mid-test."""
    monkeypatch.setattr("kensa.runner.ensure_dotenv_loaded", lambda: None)


def _stub_anthropic(monkeypatch: pytest.MonkeyPatch, captured: dict[str, object]) -> None:
    """Install a fake ``anthropic`` module whose client records all kwargs."""

    class FakeMessages:
        def create(self, **kwargs: object) -> object:
            captured.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(text="hello")])

    fake = SimpleNamespace(Anthropic=lambda: SimpleNamespace(messages=FakeMessages()))
    monkeypatch.setitem(sys.modules, "anthropic", fake)


def _stub_openai(monkeypatch: pytest.MonkeyPatch, captured: dict[str, object]) -> None:
    """Install a fake ``openai`` module whose client records all kwargs."""

    class FakeCompletions:
        def create(self, **kwargs: object) -> object:
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
            )

    fake = SimpleNamespace(
        OpenAI=lambda: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    )
    monkeypatch.setitem(sys.modules, "openai", fake)


class TestResolveProvider:
    def test_no_keys_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(RuntimeError, match="No LLM provider"):
            resolve_provider()

    def test_claude_override_routes_to_anthropic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)
        assert resolve_provider("claude-sonnet-4-6") == ("anthropic", "claude-sonnet-4-6")

    def test_anthropic_vendor_prefix_routes_to_anthropic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)
        assert resolve_provider("Anthropic/claude-foo") == ("anthropic", "Anthropic/claude-foo")

    def test_non_claude_override_routes_to_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)
        assert resolve_provider("gpt-5.4-mini") == ("openai", "gpt-5.4-mini")

    def test_env_var_override_takes_precedence_over_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KENSA_JUDGE_MODEL", "gpt-5.4-mini")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anth-key")
        assert resolve_provider() == ("openai", "gpt-5.4-mini")

    def test_anthropic_key_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anth-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert resolve_provider() == ("anthropic", None)

    def test_openai_key_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "oai-key")
        assert resolve_provider() == ("openai", None)

    def test_anthropic_key_preferred_over_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anth-key")
        monkeypatch.setenv("OPENAI_API_KEY", "oai-key")
        assert resolve_provider() == ("anthropic", None)


class TestAnthropicClientImport:
    def test_missing_package_raises_with_install_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "anthropic", None)
        with pytest.raises(ImportError, match="anthropic package required"):
            _anthropic_client()


class TestOpenAIClientImport:
    def test_missing_package_raises_with_install_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "openai", None)
        with pytest.raises(ImportError, match="openai package required"):
            _openai_client()


class TestAnthropicCompleter:
    def test_plain_completion_sends_max_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}
        _stub_anthropic(monkeypatch, captured)

        completer = AnthropicCompleter(model="claude-sonnet-4-6", max_tokens=512)
        text = completer.complete("say hi")

        assert text == "hello"
        assert captured["model"] == "claude-sonnet-4-6"
        assert captured["max_tokens"] == 512
        assert captured["messages"] == [{"role": "user", "content": "say hi"}]
        assert "system" not in captured

    def test_json_mode_injects_system_prompt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}
        _stub_anthropic(monkeypatch, captured)

        completer = AnthropicCompleter()
        completer.complete("give json", response_format="json")

        system_prompt = captured.get("system")
        assert isinstance(system_prompt, str)
        assert "JSON" in system_prompt

    def test_returns_empty_when_no_text_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeMessages:
            def create(self, **kwargs: object) -> object:
                return SimpleNamespace(content=[SimpleNamespace()])

        fake = SimpleNamespace(Anthropic=lambda: SimpleNamespace(messages=FakeMessages()))
        monkeypatch.setitem(sys.modules, "anthropic", fake)

        assert AnthropicCompleter().complete("hi") == ""


class TestOpenAICompleter:
    def test_uses_max_completion_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}
        _stub_openai(monkeypatch, captured)

        completer = OpenAICompleter(model="gpt-5.4-mini", max_tokens=512)
        text = completer.complete("say hi")

        assert text == '{"ok": true}'
        assert captured["model"] == "gpt-5.4-mini"
        assert captured["max_completion_tokens"] == 512
        assert "max_tokens" not in captured
        assert "response_format" not in captured

    def test_json_mode_sets_response_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}
        _stub_openai(monkeypatch, captured)

        OpenAICompleter().complete("give json", response_format="json")

        assert captured["response_format"] == {"type": "json_object"}

    def test_returns_empty_string_when_content_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeCompletions:
            def create(self, **kwargs: object) -> object:
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
                )

        fake = SimpleNamespace(
            OpenAI=lambda: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
        )
        monkeypatch.setitem(sys.modules, "openai", fake)

        assert OpenAICompleter().complete("hi") == ""


class TestGetCompleter:
    def test_routes_to_anthropic_from_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anth-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        _stub_anthropic(monkeypatch, {})

        completer = get_completer()
        assert isinstance(completer, AnthropicCompleter)
        assert completer.model == "claude-sonnet-4-6"

    def test_routes_to_openai_from_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "oai-key")
        _stub_openai(monkeypatch, {})

        completer = get_completer()
        assert isinstance(completer, OpenAICompleter)
        assert completer.model == "gpt-5.4-mini"

    def test_explicit_model_overrides_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anth-key")
        _stub_openai(monkeypatch, {})

        completer = get_completer("gpt-5.4-mini")
        assert isinstance(completer, OpenAICompleter)
        assert completer.model == "gpt-5.4-mini"

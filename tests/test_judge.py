"""Tests for the judge module."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from kensa.judge import (
    _build_trace_summary,
    _error_result_for_run,
    _extract_expected,
    _format_structured_criteria,
    _parse_judge_response,
    build_judge_prompt,
    get_judge,
    judge_manifest,
    judge_scenario,
    load_judge_prompt_spec,
    manifest_requires_judge,
)
from kensa.models import (
    JudgePromptExample,
    JudgePromptSpec,
    JudgeResult,
    ResultStatus,
    RunManifest,
    Scenario,
    ScenarioRun,
    Span,
)


class MockJudge:
    """Mock judge that returns a configurable result."""

    def __init__(
        self,
        passed: bool = True,
        reasoning: str = "Looks good",
        verdict: ResultStatus | None = None,
    ) -> None:
        self.passed = passed
        self.reasoning = reasoning
        self.verdict = verdict
        self.call_count = 0
        self.last_prompt: str | None = None

    def judge(self, prompt: str) -> JudgeResult:
        self.call_count += 1
        self.last_prompt = prompt
        return JudgeResult(
            passed=self.passed,
            reasoning=self.reasoning,
            verdict=self.verdict or (ResultStatus.PASS if self.passed else ResultStatus.FAIL),
        )


class TestParseJudgeResponse:
    def test_valid_json(self) -> None:
        result = _parse_judge_response('{"passed": true, "reasoning": "All good"}')
        assert result.passed is True
        assert result.reasoning == "All good"
        assert result.verdict == ResultStatus.PASS

    def test_json_in_code_fence(self) -> None:
        text = '```json\n{"passed": false, "reasoning": "Failed"}\n```'
        result = _parse_judge_response(text)
        assert result.passed is False
        assert result.reasoning == "Failed"
        assert result.verdict == ResultStatus.FAIL

    def test_invalid_json(self) -> None:
        result = _parse_judge_response("not json at all")
        assert result.passed is False
        assert "Failed to parse" in result.reasoning
        assert result.verdict == ResultStatus.FAIL

    def test_missing_fields(self) -> None:
        result = _parse_judge_response("{}")
        assert result.passed is False

    def test_verdict_pass(self) -> None:
        text = '{"verdict": "pass", "reasoning": "ok", "evidence": ["found output"]}'
        result = _parse_judge_response(text)
        assert result.passed is True
        assert result.verdict == ResultStatus.PASS
        assert result.evidence == ["found output"]

    def test_verdict_fail(self) -> None:
        text = '{"verdict": "fail", "reasoning": "wrong", "evidence": ["missing output"]}'
        result = _parse_judge_response(text)
        assert result.passed is False
        assert result.verdict == ResultStatus.FAIL

    def test_verdict_uncertain(self) -> None:
        text = '{"verdict": "uncertain", "reasoning": "ambiguous", "evidence": ["unclear"]}'
        result = _parse_judge_response(text)
        assert result.passed is False
        assert result.verdict == ResultStatus.UNCERTAIN
        assert result.evidence == ["unclear"]

    def test_verdict_overrides_passed_bool(self) -> None:
        """When verdict is present, it takes precedence over the passed field."""
        text = '{"verdict": "fail", "passed": true, "reasoning": "conflict"}'
        result = _parse_judge_response(text)
        assert result.passed is False
        assert result.verdict == ResultStatus.FAIL

    def test_evidence_non_list_ignored(self) -> None:
        text = '{"verdict": "pass", "reasoning": "ok", "evidence": "not a list"}'
        result = _parse_judge_response(text)
        assert result.evidence == []

    def test_evidence_items_coerced_to_str(self) -> None:
        text = '{"verdict": "pass", "reasoning": "ok", "evidence": [1, true, "text"]}'
        result = _parse_judge_response(text)
        assert result.evidence == ["1", "True", "text"]

    def test_unknown_verdict_falls_back(self) -> None:
        """Unknown verdict string treated as not-passed with no verdict set."""
        text = '{"verdict": "maybe", "reasoning": "dunno"}'
        result = _parse_judge_response(text)
        assert result.passed is False
        assert result.verdict is None


class TestBuildJudgePrompt:
    def test_includes_scenario_info(
        self, sample_scenario: Scenario, sample_spans: list[Span]
    ) -> None:
        prompt = build_judge_prompt(sample_scenario, sample_spans)
        assert "Weather query smoke test" in prompt
        assert "weather information" in prompt
        assert "Evaluation Criteria" in prompt

    def test_includes_trace_summary(
        self, sample_scenario: Scenario, sample_spans: list[Span]
    ) -> None:
        prompt = build_judge_prompt(sample_scenario, sample_spans)
        assert "LLM calls" in prompt or "llm" in prompt.lower()


class TestJudgeScenario:
    def test_all_checks_pass_judge_passes(
        self, sample_scenario: Scenario, sample_spans: list[Span]
    ) -> None:
        mock = MockJudge(passed=True)
        result = judge_scenario(sample_scenario, sample_spans, "traces/test.jsonl", mock)
        assert result.status == ResultStatus.PASS
        assert mock.call_count == 1

    def test_all_checks_pass_judge_fails(
        self, sample_scenario: Scenario, sample_spans: list[Span]
    ) -> None:
        mock = MockJudge(passed=False, reasoning="Output was wrong")
        result = judge_scenario(sample_scenario, sample_spans, "traces/test.jsonl", mock)
        assert result.status == ResultStatus.FAIL
        assert result.judge_result is not None
        assert not result.judge_result.passed

    def test_check_fails_skips_judge(
        self, sample_scenario: Scenario, sample_spans: list[Span]
    ) -> None:
        # Add a check that will fail
        from kensa.models import Check, CheckType

        scenario = sample_scenario.model_copy(
            update={
                "checks": [
                    Check(
                        type=CheckType.OUTPUT_CONTAINS,
                        params={"value": "NONEXISTENT_STRING"},
                    )
                ]
            }
        )
        mock = MockJudge()
        result = judge_scenario(scenario, sample_spans, "traces/test.jsonl", mock)
        assert result.status == ResultStatus.FAIL
        assert mock.call_count == 0  # Judge was NOT called

    def test_no_criteria_skips_judge(self, sample_spans: list[Span]) -> None:
        from kensa.models import Scenario

        scenario = Scenario(
            id="no_criteria",
            name="No criteria test",
            run_command=["echo", "test"],
            criteria=None,
        )
        mock = MockJudge()
        result = judge_scenario(scenario, sample_spans, "traces/test.jsonl", mock)
        assert result.status == ResultStatus.PASS
        assert mock.call_count == 0

    def test_uncertain_verdict_returns_uncertain_status(
        self, sample_scenario: Scenario, sample_spans: list[Span]
    ) -> None:
        mock = MockJudge(passed=False, reasoning="Ambiguous", verdict=ResultStatus.UNCERTAIN)
        result = judge_scenario(sample_scenario, sample_spans, "traces/test.jsonl", mock)
        assert result.status == ResultStatus.UNCERTAIN
        assert result.judge_result is not None
        assert result.judge_result.verdict == ResultStatus.UNCERTAIN

    def test_trace_summary_populated(
        self, sample_scenario: Scenario, sample_spans: list[Span]
    ) -> None:
        mock = MockJudge()
        result = judge_scenario(sample_scenario, sample_spans, "traces/test.jsonl", mock)
        assert result.trace is not None
        assert result.trace.llm_calls == 1
        assert result.trace.tool_calls == 1
        assert result.trace.total_tokens == 35

    def test_judge_error_returns_error_status(
        self, sample_scenario: Scenario, sample_spans: list[Span]
    ) -> None:
        class FailingJudge:
            def judge(self, prompt: str) -> JudgeResult:
                raise RuntimeError("API timeout")

        result = judge_scenario(sample_scenario, sample_spans, "traces/test.jsonl", FailingJudge())
        assert result.status == ResultStatus.ERROR
        assert result.error is not None
        assert "Judge error" in result.error


class TestBuildTraceSummary:
    def test_basic(self, sample_spans: list[Span]) -> None:
        summary = _build_trace_summary(sample_spans, "test.jsonl")
        assert summary.path == "test.jsonl"
        assert summary.llm_calls == 1
        assert summary.tool_calls == 1
        assert summary.total_tokens == 35
        assert summary.cost_usd == 0.003
        assert summary.duration_seconds > 0

    def test_empty_spans(self) -> None:
        summary = _build_trace_summary([], "empty.jsonl")
        assert summary.llm_calls == 0
        assert summary.duration_seconds == 0.0


class TestGetJudge:
    def test_no_keys_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)
        # Prevent load_dotenv from finding the project .env
        monkeypatch.chdir(tmp_path)
        with pytest.raises(RuntimeError, match="No judge model"):
            get_judge()

    def test_model_override_routes_to_anthropic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Model name containing 'claude' should route to AnthropicJudge."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)

        from kensa.judge import AnthropicJudge

        try:
            judge = get_judge("claude-sonnet-4-6")
            assert isinstance(judge, AnthropicJudge)
        except ImportError:
            pass  # anthropic package not installed

    def test_model_override_routes_to_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Model name not containing 'claude' should route to OpenAIJudge."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)

        from kensa.judge import OpenAIJudge

        try:
            judge = get_judge("gpt-5.4-mini")
            assert isinstance(judge, OpenAIJudge)
        except (ImportError, Exception):
            pass  # openai package issues

    def test_env_var_anthropic_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)
        try:
            judge = get_judge()
            from kensa.judge import AnthropicJudge

            assert isinstance(judge, AnthropicJudge)
        except ImportError:
            pass  # OK if anthropic not installed

    def test_env_var_openai_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)
        try:
            judge = get_judge()
            from kensa.judge import OpenAIJudge

            assert isinstance(judge, OpenAIJudge)
        except (ImportError, Exception):
            pass  # OK if openai not installed


class TestManifestRequiresJudge:
    def test_checks_only_manifest_returns_false(
        self, tmp_path: Path, sample_scenario: Scenario
    ) -> None:
        scenario = sample_scenario.model_copy(update={"criteria": None, "judge": None})
        (tmp_path / "smoke_test.yaml").write_text(scenario.model_dump_json())
        manifest = RunManifest(
            run_id="run_1",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            scenarios={
                "smoke_test": [
                    ScenarioRun(trace_path="trace.jsonl", exit_code=0, duration_seconds=1.0)
                ]
            },
        )
        assert manifest_requires_judge(manifest, tmp_path) is False

    def test_manifest_with_criteria_returns_true(
        self, tmp_path: Path, sample_scenario: Scenario
    ) -> None:
        (tmp_path / "smoke_test.yaml").write_text(sample_scenario.model_dump_json())
        manifest = RunManifest(
            run_id="run_1",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            scenarios={
                "smoke_test": [
                    ScenarioRun(trace_path="trace.jsonl", exit_code=0, duration_seconds=1.0)
                ]
            },
        )
        assert manifest_requires_judge(manifest, tmp_path) is True


class TestJudgeManifestFailures:
    def test_includes_failed_run_without_trace(
        self, tmp_path: Path, sample_scenario: Scenario
    ) -> None:
        (tmp_path / "smoke_test.yaml").write_text(sample_scenario.model_dump_json())
        manifest = RunManifest(
            run_id="run_1",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            scenarios={
                "smoke_test": [
                    ScenarioRun(
                        trace_path="",
                        exit_code=-1,
                        duration_seconds=0.0,
                        stderr="crashed early",
                    )
                ]
            },
        )

        results, skipped = judge_manifest(manifest, judge_provider=None, scenario_dir=tmp_path)

        assert skipped == []
        assert len(results) == 1
        assert results[0].status == ResultStatus.ERROR
        assert results[0].error is not None
        assert "crashed early" in results[0].error

    def test_marks_traced_subprocess_failure_as_error(
        self,
        tmp_path: Path,
        sample_scenario: Scenario,
        sample_spans: list[Span],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "smoke_test.yaml").write_text(sample_scenario.model_dump_json())
        manifest = RunManifest(
            run_id="run_1",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            scenarios={
                "smoke_test": [
                    ScenarioRun(
                        trace_path="trace.jsonl",
                        exit_code=2,
                        duration_seconds=0.5,
                        stderr="stacktrace",
                    )
                ]
            },
        )
        monkeypatch.setattr("kensa.runner.read_trace", lambda _path: sample_spans)

        results, skipped = judge_manifest(manifest, judge_provider=None, scenario_dir=tmp_path)

        assert skipped == []
        assert len(results) == 1
        assert results[0].status == ResultStatus.ERROR
        assert results[0].trace is not None
        assert results[0].error is not None
        assert "code 2" in results[0].error


class TestBuildJudgePromptEdgeCases:
    def test_long_output_truncated(self, sample_scenario: Scenario) -> None:
        from datetime import datetime, timezone

        from kensa.models import SpanKind

        span = Span(
            trace_id="t1",
            span_id="s1",
            name="test",
            kind=SpanKind.LLM,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            output={"messages": [{"role": "assistant", "content": "x" * 1000}]},
        )
        prompt = build_judge_prompt(sample_scenario, [span])
        assert "..." in prompt  # Output was truncated

    def test_error_span_marked(self, sample_scenario: Scenario) -> None:
        from datetime import datetime, timezone

        from kensa.models import SpanKind

        span = Span(
            trace_id="t1",
            span_id="s1",
            name="test",
            kind=SpanKind.LLM,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            status="error",
        )
        prompt = build_judge_prompt(sample_scenario, [span])
        assert "[ERROR]" in prompt

    def test_no_criteria(self) -> None:
        scenario = Scenario(id="test", name="Test", criteria=None)
        from datetime import datetime, timezone

        from kensa.models import SpanKind

        span = Span(
            trace_id="t1",
            span_id="s1",
            name="test",
            kind=SpanKind.LLM,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        prompt = build_judge_prompt(scenario, [span])
        assert "No specific criteria" in prompt


class TestStructuredJudgePrompt:
    def test_load_judge_prompt_spec(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        judge_file = tmp_path / "tone.yaml"
        judge_file.write_text(
            "criterion: Tone matches persona\n"
            "pass: Formal language for luxury clients\n"
            "fail: Casual slang for luxury clients\n"
            "examples:\n"
            "  - output: Dear Mr. Harrington...\n"
            "    label: pass\n"
            "    critique: Formal salutation.\n"
        )
        monkeypatch.setattr("kensa.paths.JUDGE_DIR", tmp_path)
        load_judge_prompt_spec.cache_clear()
        spec = load_judge_prompt_spec("tone")
        assert spec.criterion == "Tone matches persona"
        assert spec.pass_definition == "Formal language for luxury clients"
        assert spec.fail_definition == "Casual slang for luxury clients"
        assert len(spec.examples) == 1

    def test_load_judge_prompt_spec_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_judge_prompt_spec("nonexistent_judge_prompt")

    def test_format_structured_criteria_with_examples(self) -> None:
        spec = JudgePromptSpec(
            criterion="Accuracy",
            pass_definition="Correct answer",
            fail_definition="Wrong answer",
            examples=[
                JudgePromptExample(output="42", label="pass", critique="Correct."),
                JudgePromptExample(output="99", label="fail", critique="Wrong number."),
            ],
        )
        text = _format_structured_criteria(spec)
        assert "Criterion: Accuracy" in text
        assert "PASS: Correct answer" in text
        assert "FAIL: Wrong answer" in text
        assert "Example 1 [PASS]" in text
        assert "Example 2 [FAIL]" in text
        assert "Output: 42" in text

    def test_format_structured_criteria_no_examples(self) -> None:
        spec = JudgePromptSpec(
            criterion="Speed",
            pass_definition="Under 2 seconds",
            fail_definition="Over 2 seconds",
        )
        text = _format_structured_criteria(spec)
        assert "Criterion: Speed" in text
        assert "Examples" not in text

    def test_build_judge_prompt_with_judge_field(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import datetime, timezone

        from kensa.models import SpanKind

        judge_file = tmp_path / "accuracy.yaml"
        judge_file.write_text(
            "criterion: Response is factually correct\n"
            "pass: All facts match source data\n"
            "fail: Contains fabricated information\n"
        )
        monkeypatch.setattr("kensa.paths.JUDGE_DIR", tmp_path)
        load_judge_prompt_spec.cache_clear()
        scenario = Scenario(id="test", name="Test", judge="accuracy")
        span = Span(
            trace_id="t1",
            span_id="s1",
            name="test",
            kind=SpanKind.LLM,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        prompt = build_judge_prompt(scenario, [span])
        assert "Criterion: Response is factually correct" in prompt
        assert "PASS: All facts match source data" in prompt
        assert "FAIL: Contains fabricated information" in prompt

    def test_judge_scenario_triggers_on_judge_field(self, sample_spans: list[Span]) -> None:
        scenario = Scenario(
            id="judge_ref",
            name="Judge ref test",
            run_command=["echo", "test"],
            judge="nonexistent",
        )
        mock = MockJudge()
        # judge field is set but file doesn't exist, so it should error
        result = judge_scenario(scenario, sample_spans, "traces/test.jsonl", mock)
        assert result.status == ResultStatus.ERROR
        assert result.error is not None
        assert "Judge error" in result.error


class TestSubstituteParams:
    """Tests for {{field}} placeholder substitution in check params."""

    def test_no_row_returns_params_unchanged(self) -> None:
        from kensa.judge import _substitute_params

        params = {"value": "{{expected}}"}
        assert _substitute_params(params, None) == params

    def test_simple_substitution(self) -> None:
        from kensa.judge import _substitute_params

        row = {"expected": "P1", "ticket": "SSO is down"}
        params = {"value": "{{expected}}"}
        assert _substitute_params(params, row) == {"value": "P1"}

    def test_multiple_fields_in_one_value(self) -> None:
        from kensa.judge import _substitute_params

        row = {"label": "P1", "confidence": "high"}
        params = {"value": "{{label}}-{{confidence}}"}
        assert _substitute_params(params, row) == {"value": "P1-high"}

    def test_non_string_params_unchanged(self) -> None:
        from kensa.judge import _substitute_params

        row = {"expected": "P1"}
        params = {"value": "{{expected}}", "max": 0.05, "flag": True}
        result = _substitute_params(params, row)
        assert result == {"value": "P1", "max": 0.05, "flag": True}

    def test_no_placeholder_returns_unchanged(self) -> None:
        from kensa.judge import _substitute_params

        row = {"expected": "P1"}
        params = {"value": "literal text"}
        assert _substitute_params(params, row) == {"value": "literal text"}

    def test_missing_field_leaves_placeholder(self) -> None:
        from kensa.judge import _substitute_params

        row = {"ticket": "test"}
        params = {"value": "{{expected}}"}
        assert _substitute_params(params, row) == {"value": "{{expected}}"}

    def test_substitution_with_regex_pattern(self) -> None:
        from kensa.judge import _substitute_params

        row = {"expected": "P1"}
        params = {"pattern": "^{{expected}}$"}
        assert _substitute_params(params, row) == {"pattern": "^P1$"}

    def test_empty_row_returns_unchanged(self) -> None:
        from kensa.judge import _substitute_params

        params = {"value": "{{expected}}"}
        assert _substitute_params(params, {}) == params

    def test_nested_dict_substitution(self) -> None:
        from kensa.judge import _substitute_params

        row = {"token": "abc123", "host": "example.com"}
        params = {"headers": {"Authorization": "Bearer {{token}}", "Host": "{{host}}"}}
        assert _substitute_params(params, row) == {
            "headers": {"Authorization": "Bearer abc123", "Host": "example.com"},
        }

    def test_nested_list_substitution(self) -> None:
        from kensa.judge import _substitute_params

        row = {"a": "x", "b": "y"}
        params = {"values": ["{{a}}", "literal", "{{b}}"]}
        assert _substitute_params(params, row) == {"values": ["x", "literal", "y"]}

    def test_deeply_nested_substitution(self) -> None:
        from kensa.judge import _substitute_params

        row = {"val": "replaced"}
        params = {"outer": {"inner": [{"key": "{{val}}"}]}}
        assert _substitute_params(params, row) == {
            "outer": {"inner": [{"key": "replaced"}]},
        }

    def test_numeric_value_preserves_type(self) -> None:
        """A bare ``{{field}}`` placeholder must preserve the dataset value's type."""
        from kensa.judge import _substitute_params

        row = {"threshold": 0.05, "max_turns": 5}
        params = {"max_usd": "{{threshold}}", "max": "{{max_turns}}"}
        result = _substitute_params(params, row)
        assert result == {"max_usd": 0.05, "max": 5}
        assert isinstance(result["max_usd"], float)
        assert isinstance(result["max"], int)

    def test_numeric_check_threshold_from_dataset_does_not_crash(
        self,
        sample_spans: list[Span],
    ) -> None:
        """End-to-end: numeric check params from dataset must work."""
        from kensa.models import Check, CheckType

        scenario = Scenario(
            id="bounded",
            name="Bounded",
            run_command=["echo", "test"],
            checks=[
                Check(
                    type=CheckType.MAX_TURNS,
                    params={"max": "{{max_turns}}"},
                ),
            ],
        )
        row = {"max_turns": 100}
        result = judge_scenario(
            scenario,
            sample_spans,
            "traces/t.jsonl",
            dataset_row=row,
        )
        assert result.check_results[0].passed is True

    def test_embedded_placeholder_still_coerces_to_string(self) -> None:
        """When the placeholder is part of a larger string, str() coercion applies."""
        from kensa.judge import _substitute_params

        row = {"n": 3}
        params = {"label": "count={{n}}"}
        assert _substitute_params(params, row) == {"label": "count=3"}


class TestJudgeScenarioWithDatasetRow:
    """Tests for check param substitution during judge_scenario."""

    def test_check_params_substituted_from_dataset_row(
        self,
        sample_spans: list[Span],
    ) -> None:
        from kensa.models import Check, CheckType

        scenario = Scenario(
            id="triage",
            name="Triage",
            run_command=["echo", "test"],
            checks=[
                Check(
                    type=CheckType.OUTPUT_CONTAINS,
                    params={"value": "{{expected}}"},
                ),
            ],
        )
        row = {"ticket": "SSO down", "expected": "San Francisco"}
        mock = MockJudge()
        result = judge_scenario(
            scenario,
            sample_spans,
            "traces/t.jsonl",
            mock,
            dataset_row=row,
        )
        assert result.status == ResultStatus.PASS
        assert result.check_results[0].passed is True

    def test_substituted_check_can_fail(self, sample_spans: list[Span]) -> None:
        from kensa.models import Check, CheckType

        scenario = Scenario(
            id="triage",
            name="Triage",
            run_command=["echo", "test"],
            checks=[
                Check(
                    type=CheckType.OUTPUT_CONTAINS,
                    params={"value": "{{expected}}"},
                ),
            ],
        )
        row = {"ticket": "SSO down", "expected": "NONEXISTENT"}
        mock = MockJudge()
        result = judge_scenario(
            scenario,
            sample_spans,
            "traces/t.jsonl",
            mock,
            dataset_row=row,
        )
        assert result.status == ResultStatus.FAIL
        assert result.check_results[0].passed is False

    def test_no_dataset_row_leaves_placeholder_literal(
        self,
        sample_spans: list[Span],
    ) -> None:
        from kensa.models import Check, CheckType

        scenario = Scenario(
            id="triage",
            name="Triage",
            run_command=["echo", "test"],
            checks=[
                Check(
                    type=CheckType.OUTPUT_CONTAINS,
                    params={"value": "{{expected}}"},
                ),
            ],
        )
        mock = MockJudge()
        result = judge_scenario(
            scenario,
            sample_spans,
            "traces/t.jsonl",
            mock,
        )
        assert result.check_results[0].passed is False


class TestBuildJudgePromptInput:
    """Tests for scenario_input inclusion in judge prompt."""

    def test_includes_string_input(
        self,
        sample_scenario: Scenario,
        sample_spans: list[Span],
    ) -> None:
        prompt = build_judge_prompt(
            sample_scenario,
            sample_spans,
            scenario_input="SSO is down for everyone",
        )
        assert "SSO is down for everyone" in prompt
        assert "Scenario Input" in prompt

    def test_includes_dict_input(
        self,
        sample_scenario: Scenario,
        sample_spans: list[Span],
    ) -> None:
        prompt = build_judge_prompt(
            sample_scenario,
            sample_spans,
            scenario_input={"ticket": "SSO down", "priority": "high"},
        )
        assert "SSO down" in prompt
        assert "Scenario Input" in prompt

    def test_no_input_no_section(
        self,
        sample_scenario: Scenario,
        sample_spans: list[Span],
    ) -> None:
        prompt = build_judge_prompt(sample_scenario, sample_spans)
        assert "Scenario Input" not in prompt

    def test_truncates_long_input(
        self,
        sample_scenario: Scenario,
        sample_spans: list[Span],
    ) -> None:
        long_input = "x" * 3000
        prompt = build_judge_prompt(
            sample_scenario,
            sample_spans,
            scenario_input=long_input,
        )
        assert "truncated" in prompt
        assert len(prompt) < len(long_input) + 2000

    def test_includes_expected_output(
        self,
        sample_scenario: Scenario,
        sample_spans: list[Span],
    ) -> None:
        prompt = build_judge_prompt(
            sample_scenario,
            sample_spans,
            expected_output="P2",
        )
        assert "Expected Output" in prompt
        assert "P2" in prompt

    def test_no_expected_output_no_section(
        self,
        sample_scenario: Scenario,
        sample_spans: list[Span],
    ) -> None:
        prompt = build_judge_prompt(sample_scenario, sample_spans)
        assert "Expected Output" not in prompt


class TestJudgeScenarioExpectedOutput:
    """Tests that dataset_row 'expected' field reaches the judge prompt."""

    def test_expected_field_passed_to_judge(
        self,
        sample_spans: list[Span],
    ) -> None:
        scenario = Scenario(
            id="triage",
            name="Triage",
            run_command=["echo", "test"],
            criteria="Assign the correct priority.",
        )
        row = {"ticket": "SSO down", "expected": "P2"}
        mock = MockJudge()
        judge_scenario(
            scenario,
            sample_spans,
            "traces/t.jsonl",
            mock,
            dataset_row=row,
        )
        assert mock.call_count == 1
        assert mock.last_prompt is not None
        assert "Expected Output" in mock.last_prompt
        assert "P2" in mock.last_prompt

    def test_no_expected_field_no_section_in_prompt(
        self,
        sample_spans: list[Span],
    ) -> None:
        scenario = Scenario(
            id="triage",
            name="Triage",
            run_command=["echo", "test"],
            criteria="Assign the correct priority.",
        )
        row = {"ticket": "SSO down"}
        mock = MockJudge()
        judge_scenario(
            scenario,
            sample_spans,
            "traces/t.jsonl",
            mock,
            dataset_row=row,
        )
        assert mock.call_count == 1
        assert mock.last_prompt is not None
        assert "Expected Output" not in mock.last_prompt


class TestExtractExpected:
    """Tests for _extract_expected helper."""

    def test_returns_expected_when_present(self) -> None:
        assert _extract_expected({"expected": "P1", "ticket": "hi"}) == "P1"

    def test_returns_none_when_missing(self) -> None:
        assert _extract_expected({"ticket": "hi"}) is None

    def test_returns_none_for_none_row(self) -> None:
        assert _extract_expected(None) is None

    def test_returns_none_for_empty_row(self) -> None:
        assert _extract_expected({}) is None

    def test_coerces_non_string_to_str(self) -> None:
        assert _extract_expected({"expected": 42}) == "42"


class TestErrorResultForRunExpected:
    """Tests that _error_result_for_run populates expected from dataset_row."""

    def test_expected_from_dataset_row(self) -> None:
        run = ScenarioRun(
            trace_path="",
            exit_code=1,
            duration_seconds=0.0,
            dataset_row={"ticket": "SSO down", "expected": "P1"},
        )
        result = _error_result_for_run("triage", run, message="timeout")
        assert result.expected == "P1"
        assert result.status == ResultStatus.ERROR

    def test_no_dataset_row_expected_is_none(self) -> None:
        run = ScenarioRun(trace_path="", exit_code=1, duration_seconds=0.0)
        result = _error_result_for_run("triage", run, message="timeout")
        assert result.expected is None

    def test_dataset_row_without_expected_field(self) -> None:
        run = ScenarioRun(
            trace_path="",
            exit_code=1,
            duration_seconds=0.0,
            dataset_row={"ticket": "SSO down"},
        )
        result = _error_result_for_run("triage", run, message="timeout")
        assert result.expected is None


class TestJudgeScenarioExpectedOnResult:
    """Tests that judge_scenario populates Result.expected."""

    def test_expected_set_on_pass_result(self, sample_spans: list[Span]) -> None:
        scenario = Scenario(
            id="triage",
            name="Triage",
            run_command=["echo", "test"],
            criteria="Assign the correct priority.",
        )
        row = {"ticket": "SSO down", "expected": "P2"}
        mock = MockJudge()
        result = judge_scenario(
            scenario,
            sample_spans,
            "traces/t.jsonl",
            mock,
            dataset_row=row,
        )
        assert result.expected == "P2"

    def test_expected_set_on_check_fail_result(self, sample_spans: list[Span]) -> None:
        from kensa.models import Check, CheckType

        scenario = Scenario(
            id="triage",
            name="Triage",
            run_command=["echo", "test"],
            checks=[
                Check(
                    type=CheckType.OUTPUT_CONTAINS,
                    params={"value": "NONEXISTENT"},
                ),
            ],
        )
        row = {"ticket": "SSO down", "expected": "P2"}
        result = judge_scenario(
            scenario,
            sample_spans,
            "traces/t.jsonl",
            dataset_row=row,
        )
        assert result.status == ResultStatus.FAIL
        assert result.expected == "P2"

    def test_no_dataset_row_expected_is_none(self, sample_spans: list[Span]) -> None:
        scenario = Scenario(
            id="triage",
            name="Triage",
            run_command=["echo", "test"],
        )
        result = judge_scenario(
            scenario,
            sample_spans,
            "traces/t.jsonl",
        )
        assert result.expected is None

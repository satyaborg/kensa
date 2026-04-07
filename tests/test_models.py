"""Tests for kensa models: serialization round-trips, validation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kensa.models import (
    Analysis,
    Check,
    CheckType,
    Distribution,
    FlaggedTrace,
    FlagType,
    JudgePromptExample,
    JudgePromptSpec,
    Result,
    ResultStatus,
    RunManifest,
    Scenario,
    ScenarioRun,
    ScenarioSource,
    Span,
    SpanKind,
    TraceSummary,
)


class TestSpanSerialization:
    def test_round_trip_json(self, sample_llm_span: Span) -> None:
        json_str = sample_llm_span.model_dump_json()
        restored = Span.model_validate_json(json_str)
        assert restored.trace_id == sample_llm_span.trace_id
        assert restored.kind == SpanKind.LLM
        assert restored.model == "claude-sonnet-4-6"
        assert restored.tokens is not None
        assert restored.tokens.total == 35

    def test_round_trip_dict(self, sample_tool_span: Span) -> None:
        data = sample_tool_span.model_dump(mode="json")
        restored = Span.model_validate(data)
        assert restored.kind == SpanKind.TOOL
        assert len(restored.tools) == 1
        assert restored.tools[0].name == "get_weather"

    def test_minimal_span(self) -> None:
        span = Span(
            trace_id="t1",
            span_id="s1",
            name="test",
            kind=SpanKind.CHAIN,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert span.parent_span_id is None
        assert span.model is None
        assert span.tokens is None
        assert span.cost is None
        assert span.metadata == {}

    def test_span_kind_enum(self) -> None:
        for kind in SpanKind:
            span = Span(
                trace_id="t1",
                span_id="s1",
                name="test",
                kind=kind,
                start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
            assert span.kind == kind
            restored = Span.model_validate_json(span.model_dump_json())
            assert restored.kind == kind


class TestScenarioSerialization:
    def test_round_trip(self, sample_scenario: Scenario) -> None:
        data = sample_scenario.model_dump(mode="json")
        restored = Scenario.model_validate(data)
        assert restored.id == "smoke_test"
        assert len(restored.checks) == 3
        assert restored.checks[0].type == CheckType.TOOL_CALLED
        assert restored.source == ScenarioSource.CODE

    def test_check_types(self) -> None:
        for ct in CheckType:
            check = Check(type=ct, params={"test": True}, description="test")
            assert check.type == ct

    def test_input_as_dict(self) -> None:
        scenario = Scenario(
            id="test",
            name="test",
            input={"key": "value"},
            run_command="echo test",
        )
        assert isinstance(scenario.input, dict)

    def test_input_as_string(self) -> None:
        scenario = Scenario(
            id="test",
            name="test",
            input="hello world",
            run_command="echo test",
        )
        assert isinstance(scenario.input, str)


class TestScenarioDatasetValidation:
    def test_both_set_is_valid(self) -> None:
        s = Scenario(
            id="ds",
            name="Dataset test",
            run_command="echo {{input}}",
            dataset="inputs.jsonl",
            input_field="query",
        )
        assert s.dataset == "inputs.jsonl"
        assert s.input_field == "query"

    def test_neither_set_is_valid(self) -> None:
        s = Scenario(id="no_ds", name="No dataset", run_command="echo hello")
        assert s.dataset is None
        assert s.input_field is None

    def test_dataset_without_input_field_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="must both be set"):
            Scenario(
                id="bad",
                name="Bad",
                run_command="echo {{input}}",
                dataset="inputs.jsonl",
            )

    def test_input_field_without_dataset_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="must both be set"):
            Scenario(
                id="bad",
                name="Bad",
                run_command="echo {{input}}",
                input_field="query",
            )


class TestResultSerialization:
    def test_pass_result(self) -> None:
        result = Result(
            scenario_id="test",
            status=ResultStatus.PASS,
            trace=TraceSummary(
                path="traces/test.jsonl",
                llm_calls=2,
                tool_calls=1,
                total_tokens=100,
                cost_usd=0.005,
                duration_seconds=3.2,
            ),
        )
        data = result.model_dump(mode="json")
        restored = Result.model_validate(data)
        assert restored.status == ResultStatus.PASS
        assert restored.trace is not None
        assert restored.trace.cost_usd == 0.005

    def test_fail_result_with_error(self) -> None:
        result = Result(
            scenario_id="test",
            status=ResultStatus.ERROR,
            error="Something went wrong",
        )
        json_str = result.model_dump_json()
        restored = Result.model_validate_json(json_str)
        assert restored.error == "Something went wrong"


class TestAnalysisSerialization:
    def test_round_trip(self) -> None:
        analysis = Analysis(
            trace_count=10,
            success_rate=0.8,
            cost_distribution=Distribution(p50=0.01, p90=0.05, p99=0.1, max=0.15),
            flagged_traces=[
                FlaggedTrace(trace_id="t1", flag=FlagType.COST_OUTLIER, detail="expensive"),
            ],
        )
        data = analysis.model_dump(mode="json")
        restored = Analysis.model_validate(data)
        assert restored.trace_count == 10
        assert len(restored.flagged_traces) == 1
        assert restored.flagged_traces[0].flag == FlagType.COST_OUTLIER


class TestRunManifest:
    def test_round_trip(self) -> None:
        manifest = RunManifest(
            run_id="20260317T143000",
            timestamp=datetime(2026, 3, 17, 14, 30, 0, tzinfo=timezone.utc),
            scenarios={
                "smoke_test": [
                    ScenarioRun(
                        trace_path=".kensa/traces/smoke_test.jsonl",
                        exit_code=0,
                        duration_seconds=4.2,
                    ),
                ],
            },
        )
        restored = RunManifest.model_validate_json(manifest.model_dump_json())
        assert restored.run_id == "20260317T143000"
        assert "smoke_test" in restored.scenarios
        assert len(restored.scenarios["smoke_test"]) == 1
        assert restored.scenarios["smoke_test"][0].exit_code == 0

    def test_migration_single_to_list(self) -> None:
        """Old format (single ScenarioRun dict) auto-migrates to list."""
        old_data = {
            "run_id": "20260317T143000",
            "timestamp": "2026-03-17T14:30:00+00:00",
            "scenarios": {
                "test_1": {
                    "trace_path": "t/test.jsonl",
                    "exit_code": 0,
                    "duration_seconds": 1.0,
                    "stdout": "",
                    "stderr": "",
                },
            },
        }
        manifest = RunManifest.model_validate(old_data)
        assert isinstance(manifest.scenarios["test_1"], list)
        assert len(manifest.scenarios["test_1"]) == 1
        assert manifest.scenarios["test_1"][0].exit_code == 0

    def test_migration_already_list(self) -> None:
        """Already-migrated format (list) is left unchanged."""
        data = {
            "run_id": "20260317T143000",
            "timestamp": "2026-03-17T14:30:00+00:00",
            "scenarios": {
                "test_1": [
                    {"trace_path": "t/a.jsonl", "exit_code": 0, "duration_seconds": 1.0},
                    {"trace_path": "t/b.jsonl", "exit_code": 0, "duration_seconds": 2.0},
                ],
            },
        }
        manifest = RunManifest.model_validate(data)
        assert len(manifest.scenarios["test_1"]) == 2


class TestScenarioJudgeValidation:
    def test_judge_only_is_valid(self) -> None:
        s = Scenario(id="j", name="Judge test", run_command="echo test", judge="tone-match")
        assert s.judge == "tone-match"
        assert s.criteria is None

    def test_criteria_only_is_valid(self) -> None:
        s = Scenario(id="c", name="Criteria test", run_command="echo test", criteria="Be accurate")
        assert s.criteria == "Be accurate"
        assert s.judge is None

    def test_neither_is_valid(self) -> None:
        s = Scenario(id="n", name="No judge", run_command="echo test")
        assert s.criteria is None
        assert s.judge is None

    def test_both_set_raises(self) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            Scenario(
                id="bad",
                name="Bad",
                run_command="echo test",
                criteria="Be accurate",
                judge="tone-match",
            )


class TestJudgePromptSpec:
    def test_from_dict_with_pass_fail_keys(self) -> None:
        data = {
            "criterion": "Tone matches client persona",
            "pass": "Formal language for luxury clients",
            "fail": "Casual slang for luxury clients",
            "examples": [
                {
                    "output": "Dear Mr. Harrington...",
                    "label": "pass",
                    "critique": "Formal salutation, luxury positioning.",
                },
            ],
        }
        spec = JudgePromptSpec.model_validate(data)
        assert spec.criterion == "Tone matches client persona"
        assert spec.pass_definition == "Formal language for luxury clients"
        assert spec.fail_definition == "Casual slang for luxury clients"
        assert len(spec.examples) == 1
        assert spec.examples[0].label == "pass"

    def test_from_dict_with_explicit_field_names(self) -> None:
        spec = JudgePromptSpec(
            criterion="Test",
            pass_definition="It works",
            fail_definition="It breaks",
        )
        assert spec.pass_definition == "It works"
        assert spec.fail_definition == "It breaks"

    def test_no_examples_is_valid(self) -> None:
        data = {"criterion": "Test", "pass": "Good", "fail": "Bad"}
        spec = JudgePromptSpec.model_validate(data)
        assert spec.examples == []

    def test_round_trip_json(self) -> None:
        spec = JudgePromptSpec(
            criterion="Accuracy",
            pass_definition="Correct answer",
            fail_definition="Wrong answer",
            examples=[
                JudgePromptExample(output="42", label="pass", critique="Correct."),
            ],
        )
        data = spec.model_dump(mode="json")
        restored = JudgePromptSpec.model_validate(data)
        assert restored.criterion == spec.criterion
        assert restored.pass_definition == spec.pass_definition
        assert len(restored.examples) == 1

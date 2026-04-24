"""Tests for kensa models: serialization round-trips, validation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kensa.models import (
    Analysis,
    Check,
    CheckResult,
    CheckType,
    Distribution,
    FlaggedTrace,
    FlagType,
    JudgePromptExample,
    JudgePromptSpec,
    Result,
    ResultStatus,
    RunKind,
    RunManifest,
    Scenario,
    ScenarioRun,
    ScenarioSource,
    Span,
    SpanKind,
    TraceSummary,
    TrajectoryArgsMode,
    TrajectoryOrderingMode,
    TrajectoryParams,
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
        assert restored.checks[0].type == CheckType.TOOLS_CALLED
        assert restored.source == ScenarioSource.CODE

    def test_check_types(self) -> None:
        # Checks with list-param validation need valid params.
        valid_params: dict[CheckType, dict[str, object]] = {
            CheckType.TOOLS_CALLED: {"tools": ["t"]},
            CheckType.TOOLS_NOT_CALLED: {"tools": ["t"]},
            CheckType.TOOL_ORDER: {"order": ["t"]},
            CheckType.TRAJECTORY: {"steps": [{"tool": "search_docs"}]},
        }
        for ct in CheckType:
            params = valid_params.get(ct, {"test": True})
            check = Check(type=ct, params=params, description="test")
            assert check.type == ct

    def test_input_defaults_to_none(self) -> None:
        scenario = Scenario(id="test", name="test", run_command=["echo", "test"])
        assert scenario.input is None

    def test_input_as_dict(self) -> None:
        scenario = Scenario(
            id="test",
            name="test",
            input={"key": "value"},
            run_command=["echo", "test"],
        )
        assert isinstance(scenario.input, dict)

    def test_input_as_string(self) -> None:
        scenario = Scenario(
            id="test",
            name="test",
            input="hello world",
            run_command=["echo", "test"],
        )
        assert isinstance(scenario.input, str)

    def test_input_as_empty_string_is_preserved(self) -> None:
        scenario = Scenario(
            id="test",
            name="test",
            input="",
            run_command=["echo", "test"],
        )
        assert scenario.input == ""


class TestScenarioDatasetValidation:
    def test_both_set_is_valid(self) -> None:
        s = Scenario(
            id="ds",
            name="Dataset test",
            run_command=["echo"],
            dataset="inputs.jsonl",
            input_field="query",
        )
        assert s.dataset == "inputs.jsonl"
        assert s.input_field == "query"

    def test_neither_set_is_valid(self) -> None:
        s = Scenario(id="no_ds", name="No dataset", run_command=["echo", "hello"])
        assert s.dataset is None
        assert s.input_field is None

    def test_dataset_without_input_field_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="must both be set"):
            Scenario(
                id="bad",
                name="Bad",
                run_command=["echo"],
                dataset="inputs.jsonl",
            )

    def test_input_field_without_dataset_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="must both be set"):
            Scenario(
                id="bad",
                name="Bad",
                run_command=["echo"],
                input_field="query",
            )


class TestCheckParamValidation:
    """Validate that Check._validate_params catches bad param shapes at parse time."""

    @pytest.mark.parametrize(
        ("check_type", "key"),
        [
            (CheckType.TOOLS_CALLED, "tools"),
            (CheckType.TOOLS_NOT_CALLED, "tools"),
            (CheckType.TOOL_ORDER, "order"),
        ],
    )
    def test_scalar_string_instead_of_list(self, check_type: CheckType, key: str) -> None:
        with pytest.raises(ValueError, match="must be a list of strings, got a bare string"):
            Check(type=check_type, params={key: "search"})

    @pytest.mark.parametrize(
        ("check_type", "key"),
        [
            (CheckType.TOOLS_CALLED, "tools"),
            (CheckType.TOOLS_NOT_CALLED, "tools"),
            (CheckType.TOOL_ORDER, "order"),
        ],
    )
    def test_missing_required_key(self, check_type: CheckType, key: str) -> None:
        del key
        with pytest.raises(ValueError, match="missing required"):
            Check(type=check_type, params={})

    @pytest.mark.parametrize(
        ("check_type", "key"),
        [
            (CheckType.TOOLS_CALLED, "tools"),
            (CheckType.TOOLS_NOT_CALLED, "tools"),
            (CheckType.TOOL_ORDER, "order"),
        ],
    )
    def test_empty_list(self, check_type: CheckType, key: str) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            Check(type=check_type, params={key: []})

    @pytest.mark.parametrize(
        ("check_type", "key"),
        [
            (CheckType.TOOLS_CALLED, "tools"),
            (CheckType.TOOLS_NOT_CALLED, "tools"),
            (CheckType.TOOL_ORDER, "order"),
        ],
    )
    def test_non_string_items(self, check_type: CheckType, key: str) -> None:
        with pytest.raises(ValueError, match="must contain only strings"):
            Check(type=check_type, params={key: [1, 2]})

    @pytest.mark.parametrize(
        ("check_type", "key"),
        [
            (CheckType.TOOLS_CALLED, "tools"),
            (CheckType.TOOLS_NOT_CALLED, "tools"),
            (CheckType.TOOL_ORDER, "order"),
        ],
    )
    def test_valid_single_item(self, check_type: CheckType, key: str) -> None:
        check = Check(type=check_type, params={key: ["search"]})
        assert check.params[key] == ["search"]

    @pytest.mark.parametrize(
        ("check_type", "key"),
        [
            (CheckType.TOOLS_CALLED, "tools"),
            (CheckType.TOOLS_NOT_CALLED, "tools"),
            (CheckType.TOOL_ORDER, "order"),
        ],
    )
    def test_valid_multi_item(self, check_type: CheckType, key: str) -> None:
        check = Check(type=check_type, params={key: ["search", "fetch"]})
        assert check.params[key] == ["search", "fetch"]

    def test_scalar_hint_includes_tool_name(self) -> None:
        """Error message should suggest the fix: Use ['search'] for a single tool."""
        with pytest.raises(ValueError, match=r"Use \['search'\]"):
            Check(type=CheckType.TOOLS_CALLED, params={"tools": "search"})

    def test_tool_order_scalar_hint_uses_item_language(self) -> None:
        with pytest.raises(ValueError, match=r"Use \['setup'\] for a single item\."):
            Check(type=CheckType.TOOL_ORDER, params={"order": "setup"})

    @pytest.mark.parametrize(
        ("check_type", "key"),
        [
            (CheckType.TOOLS_CALLED, "tools"),
            (CheckType.TOOLS_NOT_CALLED, "tools"),
            (CheckType.TOOL_ORDER, "order"),
        ],
    )
    def test_bare_placeholder_string_allowed_at_parse_time(
        self,
        check_type: CheckType,
        key: str,
    ) -> None:
        check = Check(type=check_type, params={key: "{{items}}"})
        assert check.params[key] == "{{items}}"

    def test_unrelated_check_type_not_validated(self) -> None:
        """Check types that don't need list params should pass without them."""
        check = Check(type=CheckType.OUTPUT_CONTAINS, params={"value": "hello"})
        assert check.type == CheckType.OUTPUT_CONTAINS

    def test_trajectory_params_valid(self) -> None:
        check = Check(
            type=CheckType.TRAJECTORY,
            params={
                "steps": [{"tool": "search_docs", "args": {"query": "refund policy"}}],
                "ordering": "any_order",
                "args": "ignore",
                "min_accuracy": 0.5,
                "max_steps": 3,
                "max_tokens": 1000,
                "max_duration_seconds": 5,
            },
        )
        params = TrajectoryParams.model_validate(check.params)
        assert params.ordering == TrajectoryOrderingMode.ANY_ORDER
        assert params.args == TrajectoryArgsMode.IGNORE

    def test_trajectory_requires_steps(self) -> None:
        with pytest.raises(ValueError, match=r"steps"):
            Check(type=CheckType.TRAJECTORY, params={})

    def test_trajectory_steps_must_not_be_empty(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            Check(type=CheckType.TRAJECTORY, params={"steps": []})

    def test_trajectory_min_accuracy_bounds(self) -> None:
        with pytest.raises(ValueError, match=r"between 0\.0 and 1\.0"):
            Check(
                type=CheckType.TRAJECTORY,
                params={"steps": [{"tool": "search_docs"}], "min_accuracy": 1.5},
            )

    @pytest.mark.parametrize(
        "params",
        [
            {"steps": "{{expected_steps}}", "ordering": "any_order"},
            {"steps": [{"tool": "search_docs"}], "max_steps": "{{budget}}"},
            {"steps": [{"tool": "search_docs"}], "ordering": "{{ordering}}"},
        ],
    )
    def test_trajectory_placeholder_defers_validation(self, params: dict[str, object]) -> None:
        """Trajectory checks with dataset placeholders must not fail at parse time."""
        check = Check(type=CheckType.TRAJECTORY, params=params)
        assert check.type == CheckType.TRAJECTORY


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

    def test_result_metrics_round_trip(self) -> None:
        result = Result(
            scenario_id="trajectory_test",
            status=ResultStatus.FAIL,
            check_results=[
                CheckResult(
                    check="trajectory",
                    passed=False,
                    detail="mismatch",
                    scores={"trajectory_accuracy": 0.5, "step_efficiency": 0.5},
                    diagnostics={
                        "missing_steps": [{"index": 0, "tool": "search_docs", "args": {}}]
                    },
                )
            ],
            metrics={"trajectory_accuracy": 0.5, "step_efficiency": 0.5},
        )
        restored = Result.model_validate(result.model_dump(mode="json"))
        assert restored.metrics["trajectory_accuracy"] == 0.5
        assert restored.check_results[0].diagnostics["missing_steps"][0]["tool"] == "search_docs"

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

    def test_missing_kind_defaults_to_eval(self) -> None:
        manifest = RunManifest.model_validate(
            {
                "run_id": "20260317T143000",
                "timestamp": "2026-03-17T14:30:00+00:00",
                "scenarios": {},
            }
        )
        assert manifest.kind == RunKind.EVAL

    def test_capture_manifest_round_trip(self) -> None:
        manifest = RunManifest(
            run_id="20260317T143001",
            timestamp=datetime(2026, 3, 17, 14, 30, 1, tzinfo=timezone.utc),
            kind=RunKind.CAPTURE,
            command=["python", "agent.py", "hello"],
            trace_path=".kensa/traces/20260317T143001.jsonl",
            exit_code=0,
            duration_seconds=1.5,
            stdout="ok",
            stderr="",
            span_count=3,
        )

        restored = RunManifest.model_validate_json(manifest.model_dump_json())
        assert restored.kind == RunKind.CAPTURE
        assert restored.command == ["python", "agent.py", "hello"]
        assert restored.trace_path == ".kensa/traces/20260317T143001.jsonl"
        assert restored.span_count == 3


class TestScenarioJudgeValidation:
    def test_judge_only_is_valid(self) -> None:
        s = Scenario(id="j", name="Judge test", run_command=["echo", "test"], judge="tone-match")
        assert s.judge == "tone-match"
        assert s.criteria is None

    def test_criteria_only_is_valid(self) -> None:
        s = Scenario(
            id="c", name="Criteria test", run_command=["echo", "test"], criteria="Be accurate"
        )
        assert s.criteria == "Be accurate"
        assert s.judge is None

    def test_neither_is_valid(self) -> None:
        s = Scenario(id="n", name="No judge", run_command=["echo", "test"])
        assert s.criteria is None
        assert s.judge is None

    def test_both_set_raises(self) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            Scenario(
                id="bad",
                name="Bad",
                run_command=["echo", "test"],
                criteria="Be accurate",
                judge="tone-match",
            )

    def test_multiple_trajectory_checks_raise(self) -> None:
        with pytest.raises(ValueError, match="at most one 'trajectory' check"):
            Scenario(
                id="bad_traj",
                name="Bad trajectory",
                run_command=["echo", "test"],
                checks=[
                    Check(type=CheckType.TRAJECTORY, params={"steps": [{"tool": "search"}]}),
                    Check(type=CheckType.TRAJECTORY, params={"steps": [{"tool": "fetch"}]}),
                ],
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

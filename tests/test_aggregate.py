"""Tests for kensa.aggregate: variance stats, aggregation, formatters."""

from __future__ import annotations

import json

from kensa.aggregate import (
    aggregate_all,
    aggregate_results,
    compute_estimated_pass_rate_pow_k,
    compute_variance_stats,
    format_aggregate_json,
    format_aggregate_terminal,
)
from kensa.models import (
    CheckResult,
    JudgeResult,
    Result,
    ResultStatus,
    TraceSummary,
)


class TestComputeVarianceStats:
    def test_empty(self) -> None:
        stats = compute_variance_stats([])
        assert stats.mean == 0.0
        assert stats.stddev == 0.0

    def test_single_value(self) -> None:
        stats = compute_variance_stats([5.0])
        assert stats.mean == 5.0
        assert stats.stddev == 0.0
        assert stats.min == 5.0
        assert stats.max == 5.0

    def test_multiple_values(self) -> None:
        stats = compute_variance_stats([2.0, 4.0, 6.0])
        assert stats.mean == 4.0
        assert stats.min == 2.0
        assert stats.max == 6.0
        assert stats.stddev > 0

    def test_identical_values(self) -> None:
        stats = compute_variance_stats([3.0, 3.0, 3.0])
        assert stats.mean == 3.0
        assert stats.stddev == 0.0


class TestComputeEstimatedPassRatePowK:
    def test_empty_rate(self) -> None:
        assert compute_estimated_pass_rate_pow_k(0.0) == {"3": 0.0, "5": 0.0}

    def test_non_trivial_rate(self) -> None:
        metrics = compute_estimated_pass_rate_pow_k(0.5)
        assert metrics["3"] == 0.125
        assert metrics["5"] == 0.03125


def _make_result(
    scenario_id: str = "test",
    status: ResultStatus = ResultStatus.PASS,
    check_passed: bool = True,
    judge_passed: bool = True,
    cost: float = 0.01,
    duration: float = 1.0,
) -> Result:
    return Result(
        scenario_id=scenario_id,
        status=status,
        check_results=[
            CheckResult(check="output_contains", passed=check_passed, detail="ok"),
        ],
        judge_result=JudgeResult(passed=judge_passed, reasoning="ok"),
        trace=TraceSummary(
            path="t/test.jsonl",
            llm_calls=1,
            tool_calls=0,
            total_tokens=100,
            cost_usd=cost,
            duration_seconds=duration,
        ),
    )


class TestAggregateResults:
    def test_empty(self) -> None:
        agg = aggregate_results("test", [])
        assert agg.num_runs == 0
        assert agg.pass_rate == 0.0

    def test_all_pass(self) -> None:
        results = [_make_result() for _ in range(3)]
        agg = aggregate_results("test", results)
        assert agg.num_runs == 3
        assert agg.pass_rate == 1.0
        assert agg.estimated_pass_rate_pow_k == {"3": 1.0, "5": 1.0}
        assert agg.high_variance is False
        assert agg.status_counts == {"pass": 3}

    def test_all_fail(self) -> None:
        results = [_make_result(status=ResultStatus.FAIL) for _ in range(3)]
        agg = aggregate_results("test", results)
        assert agg.pass_rate == 0.0
        assert agg.high_variance is False

    def test_mixed_high_variance(self) -> None:
        results = [
            _make_result(status=ResultStatus.PASS),
            _make_result(status=ResultStatus.FAIL),
            _make_result(status=ResultStatus.PASS),
        ]
        agg = aggregate_results("test", results)
        assert 0.6 < agg.pass_rate < 0.7
        assert agg.estimated_pass_rate_pow_k["3"] < agg.pass_rate
        assert agg.high_variance is True

    def test_cost_variance(self) -> None:
        results = [
            _make_result(cost=0.01),
            _make_result(cost=0.02),
            _make_result(cost=0.03),
        ]
        agg = aggregate_results("test", results)
        assert agg.cost.mean == 0.02
        assert agg.cost.min == 0.01
        assert agg.cost.max == 0.03

    def test_assertion_stats(self) -> None:
        results = [
            _make_result(check_passed=True, judge_passed=True),
            _make_result(check_passed=False, judge_passed=True),
            _make_result(check_passed=True, judge_passed=False),
        ]
        agg = aggregate_results("test", results)
        assert len(agg.assertion_stats) == 2  # output_contains + llm_judge

        check_stat = next(a for a in agg.assertion_stats if a.name == "output_contains")
        assert check_stat.pass_count == 2
        assert check_stat.total_count == 3

        judge_stat = next(a for a in agg.assertion_stats if a.name == "llm_judge")
        assert judge_stat.pass_count == 2
        assert judge_stat.total_count == 3

    def test_per_run_results_stored(self) -> None:
        results = [_make_result(), _make_result()]
        agg = aggregate_results("test", results)
        assert len(agg.per_run_results) == 2

    def test_uncertain_status_counted(self) -> None:
        results = [
            _make_result(status=ResultStatus.PASS),
            _make_result(status=ResultStatus.UNCERTAIN),
        ]
        agg = aggregate_results("test", results)
        assert agg.status_counts.get("uncertain") == 1
        assert agg.pass_rate == 0.5


class TestAggregateAll:
    def test_groups_by_scenario(self) -> None:
        by_scenario = {
            "a": [_make_result(scenario_id="a")],
            "b": [_make_result(scenario_id="b"), _make_result(scenario_id="b")],
        }
        aggs = aggregate_all(by_scenario)
        assert len(aggs) == 2
        assert aggs[0].scenario_id == "a"
        assert aggs[0].num_runs == 1
        assert aggs[1].scenario_id == "b"
        assert aggs[1].num_runs == 2

    def test_sorted_by_scenario_id(self) -> None:
        by_scenario = {"z": [_make_result()], "a": [_make_result()]}
        aggs = aggregate_all(by_scenario)
        assert [a.scenario_id for a in aggs] == ["a", "z"]


class TestFormatAggregateTerminal:
    def test_basic_output(self) -> None:
        results = [_make_result() for _ in range(3)]
        agg = aggregate_results("test", results)
        output = format_aggregate_terminal([agg])
        assert "test" in output
        assert "100%" in output
        assert "Est Pass^3" in output
        assert "Est Pass^5" in output
        assert "3" in output

    def test_flaky_flag(self) -> None:
        results = [
            _make_result(status=ResultStatus.PASS),
            _make_result(status=ResultStatus.FAIL),
        ]
        agg = aggregate_results("test", results)
        output = format_aggregate_terminal([agg])
        assert "FLAKY" in output

    def test_verbose_shows_assertions(self) -> None:
        results = [_make_result() for _ in range(2)]
        agg = aggregate_results("test", results)
        output = format_aggregate_terminal([agg], verbose=True)
        assert "output_contains" in output
        assert "llm_judge" in output

    def test_empty_results(self) -> None:
        output = format_aggregate_terminal([])
        assert "0/0" in output


class TestFormatAggregateJson:
    def test_valid_json(self) -> None:
        results = [_make_result() for _ in range(2)]
        agg = aggregate_results("test", results)
        output = format_aggregate_json([agg])
        data = json.loads(output)
        assert len(data) == 1
        assert data[0]["scenario_id"] == "test"
        assert data[0]["num_runs"] == 2
        assert data[0]["pass_rate"] == 1.0
        assert data[0]["estimated_pass_rate_pow_k"] == {"3": 1.0, "5": 1.0}

    def test_round_trip(self) -> None:
        from kensa.models import AggregatedResult

        results = [_make_result(), _make_result(status=ResultStatus.FAIL)]
        agg = aggregate_results("test", results)
        output = format_aggregate_json([agg])
        data = json.loads(output)
        restored = AggregatedResult.model_validate(data[0])
        assert restored.scenario_id == "test"
        assert restored.num_runs == 2
        assert restored.estimated_pass_rate_pow_k["3"] == 0.125

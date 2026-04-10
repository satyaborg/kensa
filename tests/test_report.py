"""Tests for report formatters."""

from __future__ import annotations

import json

from kensa.models import (
    CheckResult,
    JudgeResult,
    Result,
    ResultStatus,
    TraceSummary,
)
from kensa.report import format_html, format_json, format_markdown, format_terminal


def _make_results() -> list[Result]:
    return [
        Result(
            scenario_id="test_pass",
            status=ResultStatus.PASS,
            check_results=[
                CheckResult(check="output_contains", passed=True, detail="Found: 'hello'"),
            ],
            judge_result=JudgeResult(passed=True, reasoning="Correct output"),
            trace=TraceSummary(
                path="traces/pass.jsonl",
                llm_calls=2,
                tool_calls=1,
                total_tokens=100,
                cost_usd=0.005,
                duration_seconds=3.2,
            ),
        ),
        Result(
            scenario_id="test_fail",
            status=ResultStatus.FAIL,
            check_results=[
                CheckResult(check="tools_called", passed=False, detail="Missing tools: ['search']"),
            ],
            trace=TraceSummary(
                path="traces/fail.jsonl",
                llm_calls=5,
                tool_calls=0,
                total_tokens=500,
                cost_usd=0.025,
                duration_seconds=12.0,
            ),
        ),
        Result(
            scenario_id="test_error",
            status=ResultStatus.ERROR,
            error="Judge timeout",
        ),
    ]


class TestTerminalFormat:
    def test_output_contains_results(self) -> None:
        output = format_terminal(_make_results())
        assert "test_pass" in output
        assert "test_fail" in output

    def test_empty_results(self) -> None:
        output = format_terminal([])
        assert "0/0" in output


class TestMarkdownFormat:
    def test_contains_table(self) -> None:
        output = format_markdown(_make_results())
        assert "| Scenario" in output
        assert "test_pass" in output
        assert "test_fail" in output

    def test_failures_section(self) -> None:
        output = format_markdown(_make_results())
        assert "### Failures" in output
        assert "tools_called" in output

    def test_pass_only(self) -> None:
        results = [_make_results()[0]]
        output = format_markdown(results)
        assert "1/1 passed" in output
        assert "### Failures" not in output


class TestJsonFormat:
    def test_valid_json(self) -> None:
        output = format_json(_make_results())
        data = json.loads(output)
        assert len(data) == 3
        assert data[0]["scenario_id"] == "test_pass"
        assert data[0]["status"] == "pass"

    def test_round_trip(self) -> None:
        results = _make_results()
        output = format_json(results)
        data = json.loads(output)
        restored = [Result.model_validate(r) for r in data]
        assert len(restored) == len(results)
        assert restored[0].status == results[0].status


class TestTerminalEdgeCases:
    def test_failed_judge_shows_reasoning(self) -> None:
        results = [
            Result(
                scenario_id="test_judge_fail",
                status=ResultStatus.FAIL,
                check_results=[
                    CheckResult(check="output_contains", passed=True, detail="ok"),
                ],
                judge_result=JudgeResult(passed=False, reasoning="Bad output"),
            ),
        ]
        output = format_terminal(results)
        assert "test_judge_fail" in output

    def test_error_status_display(self) -> None:
        results = [
            Result(
                scenario_id="test_error",
                status=ResultStatus.ERROR,
                error="Connection timeout",
            ),
        ]
        output = format_terminal(results)
        assert "test_error" in output


class TestUncertainVerdict:
    def test_terminal_uncertain(self) -> None:
        results = [
            Result(
                scenario_id="test_uncertain",
                status=ResultStatus.UNCERTAIN,
                judge_result=JudgeResult(
                    passed=False,
                    reasoning="Ambiguous",
                    verdict=ResultStatus.UNCERTAIN,
                ),
            ),
        ]
        output = format_terminal(results)
        assert "test_uncertain" in output

    def test_markdown_uncertain_header(self) -> None:
        results = [
            Result(scenario_id="unc", status=ResultStatus.UNCERTAIN),
        ]
        output = format_markdown(results)
        assert "uncertain" in output.lower()

    def test_json_uncertain(self) -> None:
        results = [
            Result(scenario_id="unc", status=ResultStatus.UNCERTAIN),
        ]
        output = format_json(results)
        data = json.loads(output)
        assert data[0]["status"] == "uncertain"


class TestMarkdownEdgeCases:
    def test_failed_judge_in_failures_section(self) -> None:
        results = [
            Result(
                scenario_id="judge_fail",
                status=ResultStatus.FAIL,
                check_results=[],
                judge_result=JudgeResult(passed=False, reasoning="Wrong answer"),
            ),
        ]
        output = format_markdown(results)
        assert "### Failures" in output
        assert "Judge" in output
        assert "Wrong answer" in output

    def test_error_in_failures_section(self) -> None:
        results = [
            Result(
                scenario_id="err",
                status=ResultStatus.ERROR,
                error="API crash",
                trace=TraceSummary(
                    path="t.jsonl",
                    llm_calls=1,
                    tool_calls=0,
                    total_tokens=10,
                    cost_usd=0.001,
                    duration_seconds=0.5,
                ),
            ),
        ]
        output = format_markdown(results)
        assert "API crash" in output
        assert "Trace:" in output or "1 LLM" in output


def _make_result_with_expected() -> Result:
    """A failing result that has an expected value from a dataset row."""
    return Result(
        scenario_id="triage_row_1",
        status=ResultStatus.FAIL,
        input="SSO is down for everyone",
        expected="P2",
        check_results=[
            CheckResult(check="output_matches", passed=True, detail="Matched"),
        ],
        judge_result=JudgeResult(passed=False, reasoning="Wrong priority"),
        trace=TraceSummary(
            path="traces/t.jsonl",
            llm_calls=1,
            tool_calls=0,
            total_tokens=150,
            cost_usd=0.0002,
            duration_seconds=1.0,
        ),
    )


class TestExpectedInTerminal:
    def test_verbose_shows_expected(self) -> None:
        results = [_make_result_with_expected()]
        output = format_terminal(results, verbose=True)
        assert "expected: P2" in output

    def test_no_expected_omits_line(self) -> None:
        results = [_make_results()[1]]  # fail without expected
        output = format_terminal(results, verbose=True)
        assert "expected:" not in output


class TestExpectedInMarkdown:
    def test_failure_section_shows_expected(self) -> None:
        results = [_make_result_with_expected()]
        output = format_markdown(results)
        assert "**Expected**: P2" in output

    def test_no_expected_omits_line(self) -> None:
        results = [_make_results()[1]]  # fail without expected
        output = format_markdown(results)
        assert "**Expected**" not in output


class TestExpectedInJson:
    def test_expected_in_json_output(self) -> None:
        results = [_make_result_with_expected()]
        output = format_json(results)
        data = json.loads(output)
        assert data[0]["expected"] == "P2"

    def test_null_expected_in_json(self) -> None:
        results = [_make_results()[0]]
        output = format_json(results)
        data = json.loads(output)
        assert data[0]["expected"] is None


class TestExpectedInHtml:
    def test_html_renders_expected_section(self) -> None:
        results = [_make_result_with_expected()]
        output = format_html(results)
        assert "Expected" in output
        assert "P2" in output

    def test_html_no_expected_omits_section(self) -> None:
        results = [_make_results()[0]]  # pass without expected
        output = format_html(results)
        assert ">Expected<" not in output

    def test_html_escapes_expected(self) -> None:
        r = _make_result_with_expected()
        r.expected = "<b>xss</b>"
        output = format_html([r])
        assert "&lt;b&gt;xss&lt;/b&gt;" in output
        assert "<b>xss</b>" not in output

"""Pydantic models for kensa: spans, scenarios, results, analysis."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class SpanKind(str, enum.Enum):
    LLM = "llm"
    TOOL = "tool"
    AGENT = "agent"
    CHAIN = "chain"
    RETRIEVER = "retriever"
    EVALUATOR = "evaluator"


class CheckType(str, enum.Enum):
    OUTPUT_CONTAINS = "output_contains"
    OUTPUT_MATCHES = "output_matches"
    TOOLS_CALLED = "tools_called"
    TOOLS_NOT_CALLED = "tools_not_called"
    TOOL_ORDER = "tool_order"
    TRAJECTORY = "trajectory"
    MAX_COST = "max_cost"
    MAX_TURNS = "max_turns"
    MAX_DURATION = "max_duration"
    NO_REPEAT_CALLS = "no_repeat_calls"


class ScenarioSource(str, enum.Enum):
    CODE = "code"
    TRACES = "traces"
    USER = "user"


class ResultStatus(str, enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    UNCERTAIN = "uncertain"


class RunKind(str, enum.Enum):
    EVAL = "eval"
    CAPTURE = "capture"


class FlagType(str, enum.Enum):
    ERROR = "error"
    COST_OUTLIER = "cost_outlier"
    LATENCY_OUTLIER = "latency_outlier"
    REPEATED_TOOL_CALL = "repeated_tool_call"
    HIGH_TURN_COUNT = "high_turn_count"


class TokenCounts(BaseModel):
    prompt: int = 0
    completion: int = 0
    total: int = 0
    cache_read: int = 0


class CostInfo(BaseModel):
    prompt: float = 0.0
    completion: float = 0.0
    total: float = 0.0


class ToolInfo(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None


class TrajectoryOrderingMode(str, enum.Enum):
    EXACT = "exact"
    ANY_ORDER = "any_order"


class TrajectoryArgsMode(str, enum.Enum):
    EXACT = "exact"
    IGNORE = "ignore"


class TrajectoryStep(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class TrajectoryParams(BaseModel):
    steps: list[TrajectoryStep]
    ordering: TrajectoryOrderingMode = TrajectoryOrderingMode.EXACT
    args: TrajectoryArgsMode = TrajectoryArgsMode.EXACT
    min_accuracy: float = 1.0
    max_steps: int | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=0)
    max_duration_seconds: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _validate_steps_and_accuracy(self) -> TrajectoryParams:
        if not self.steps:
            raise ValueError("trajectory: 'steps' must not be empty")
        if not 0.0 <= self.min_accuracy <= 1.0:
            raise ValueError("trajectory: 'min_accuracy' must be between 0.0 and 1.0")
        return self


class Span(BaseModel):
    """A single trace span in kensa internal format."""

    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    name: str
    kind: SpanKind
    start_time: datetime
    end_time: datetime
    status: str = "ok"
    model: str | None = None
    provider: str | None = None
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    tokens: TokenCounts | None = None
    cost: CostInfo | None = None
    tools: list[ToolInfo] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


_LIST_PARAM_CHECKS: dict[CheckType, str] = {
    CheckType.TOOLS_CALLED: "tools",
    CheckType.TOOLS_NOT_CALLED: "tools",
    CheckType.TOOL_ORDER: "order",
}

_LIST_PARAM_ITEM_LABELS: dict[CheckType, str] = {
    CheckType.TOOLS_CALLED: "tool",
    CheckType.TOOLS_NOT_CALLED: "tool",
    CheckType.TOOL_ORDER: "item",
}


def _is_bare_placeholder(value: str) -> bool:
    return value.startswith("{{") and value.endswith("}}") and value.count("{{") == 1


def validate_runtime_list_params(check_type: CheckType, params: dict[str, Any]) -> None:
    key = _LIST_PARAM_CHECKS.get(check_type)
    if key is None:
        return

    check_name = check_type.value
    value = params.get(key)
    if value is None:
        raise ValueError(f"{check_name}: missing required '{key}' parameter")
    if isinstance(value, str):
        item_label = _LIST_PARAM_ITEM_LABELS.get(check_type, "item")
        raise ValueError(
            f"{check_name}: '{key}' must be a list of strings, got a bare string. "
            f"Use [{value!r}] for a single {item_label}."
        )
    if not isinstance(value, list):
        raise ValueError(
            f"{check_name}: '{key}' must be a list of strings, got {type(value).__name__}"
        )
    if not value:
        raise ValueError(f"{check_name}: '{key}' must not be empty")
    bad = [item for item in value if not isinstance(item, str)]
    if bad:
        raise ValueError(f"{check_name}: '{key}' must contain only strings, got: {bad}")


def _has_placeholder_values(params: dict[str, Any]) -> bool:
    """Check if any param value (or nested list/dict leaf) is a bare placeholder."""
    for value in params.values():
        if isinstance(value, str) and _is_bare_placeholder(value):
            return True
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and _is_bare_placeholder(item):
                    return True
                if isinstance(item, dict) and _has_placeholder_values(item):
                    return True
        if isinstance(value, dict) and _has_placeholder_values(value):
            return True
    return False


def validate_runtime_check_params(check_type: CheckType, params: dict[str, Any]) -> None:
    if check_type == CheckType.TRAJECTORY:
        if _has_placeholder_values(params):
            raise ValueError(
                "trajectory: unresolved dataset placeholders remain after substitution"
            )
        TrajectoryParams.model_validate(params)
        return
    validate_runtime_list_params(check_type, params)


class Check(BaseModel):
    """A deterministic check within a scenario."""

    type: CheckType
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""

    @model_validator(mode="after")
    def _validate_params(self) -> Check:
        if self.type == CheckType.TRAJECTORY and _has_placeholder_values(self.params):
            return self
        key = _LIST_PARAM_CHECKS.get(self.type)
        if key is not None:
            value = self.params.get(key)
            if isinstance(value, str) and _is_bare_placeholder(value):
                return self
        validate_runtime_check_params(self.type, self.params)
        return self


class JudgePromptExample(BaseModel):
    """A few-shot example for a structured judge prompt."""

    output: str
    label: Literal["pass", "fail"]
    critique: str


class JudgePromptSpec(BaseModel):
    """Structured judge prompt loaded from .kensa/judges/*.yaml."""

    criterion: str
    pass_definition: str
    fail_definition: str
    examples: list[JudgePromptExample] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _remap_reserved_keys(cls, data: Any) -> Any:
        """Remap YAML keys 'pass'/'fail' to 'pass_definition'/'fail_definition'."""
        if isinstance(data, dict):
            if "pass" in data and "pass_definition" not in data:
                data["pass_definition"] = data.pop("pass")
            if "fail" in data and "fail_definition" not in data:
                data["fail_definition"] = data.pop("fail")
        return data


class Scenario(BaseModel):
    """An eval scenario definition (loaded from YAML)."""

    id: str
    name: str
    description: str = ""
    source: ScenarioSource = ScenarioSource.CODE

    input: str | dict[str, Any] | None = None
    run_command: list[str] = Field(default_factory=list)
    env_overrides: dict[str, str] = Field(default_factory=dict)

    # Dataset expansion: reference a JSONL file for parameterized inputs.
    dataset: str | None = None
    input_field: str | None = None

    expected_outcome: str = ""
    checks: list[Check] = Field(default_factory=list)
    criteria: str | None = None
    judge: str | None = None  # reference to .kensa/judges/{name}.yaml

    trace_refs: list[str] = Field(default_factory=list)
    failure_pattern: str | None = None

    @model_validator(mode="after")
    def _dataset_requires_input_field(self) -> Scenario:
        if (self.dataset is None) != (self.input_field is None):
            raise ValueError("'dataset' and 'input_field' must both be set or both be omitted")
        return self

    @model_validator(mode="after")
    def _criteria_xor_judge(self) -> Scenario:
        if self.criteria is not None and self.judge is not None:
            raise ValueError("'criteria' and 'judge' are mutually exclusive")
        return self

    @model_validator(mode="after")
    def _single_trajectory_check(self) -> Scenario:
        trajectory_checks = sum(1 for check in self.checks if check.type == CheckType.TRAJECTORY)
        if trajectory_checks > 1:
            raise ValueError("at most one 'trajectory' check is allowed per scenario")
        return self


class CheckResult(BaseModel):
    """Result of a single deterministic check."""

    check: str
    passed: bool
    detail: str = ""
    scores: dict[str, float] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class JudgeResult(BaseModel):
    """Result from the LLM-as-judge evaluation."""

    passed: bool
    reasoning: str = ""
    verdict: ResultStatus | None = None
    evidence: list[str] = Field(default_factory=list)


class TraceSummary(BaseModel):
    """Summary statistics for a trace."""

    path: str
    llm_calls: int = 0
    tool_calls: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    duration_seconds: float = 0.0


class Result(BaseModel):
    """Complete evaluation result for a scenario."""

    scenario_id: str
    status: ResultStatus
    input: str | dict[str, Any] | None = None
    expected: str | None = None
    check_results: list[CheckResult] = Field(default_factory=list)
    judge_result: JudgeResult | None = None
    trace: TraceSummary | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    error: str | None = None


class Distribution(BaseModel):
    p50: float = 0.0
    p90: float = 0.0
    p99: float = 0.0
    max: float = 0.0


class ToolUsage(BaseModel):
    tool: str
    call_count: int = 0
    avg_latency_ms: float = 0.0
    error_rate: float = 0.0
    metrics_available: bool = True


class FlaggedTrace(BaseModel):
    trace_id: str
    flag: FlagType
    detail: str = ""


class Analysis(BaseModel):
    """Output from analyze: numerical summaries + flagged traces."""

    trace_count: int = 0
    success_rate: float = 0.0
    cost_distribution: Distribution = Field(default_factory=Distribution)
    latency_distribution: Distribution = Field(default_factory=Distribution)
    tool_usage: list[ToolUsage] = Field(default_factory=list)
    flagged_traces: list[FlaggedTrace] = Field(default_factory=list)


class ScenarioRun(BaseModel):
    """Execution metadata for a single scenario run."""

    trace_path: str
    exit_code: int
    duration_seconds: float
    stdout: str = ""
    stderr: str = ""
    input: str | dict[str, Any] | None = None
    dataset_row: dict[str, Any] | None = None


class VarianceStats(BaseModel):
    """Variance statistics for a set of numeric values."""

    mean: float = 0.0
    stddev: float = 0.0
    min: float = 0.0
    max: float = 0.0


class AssertionStat(BaseModel):
    """Pass rate for a single assertion across multiple runs."""

    name: str
    pass_count: int = 0
    total_count: int = 0
    pass_rate: float = 0.0


class AggregatedResult(BaseModel):
    """Aggregated evaluation result for a scenario across multiple runs."""

    scenario_id: str
    num_runs: int = 1
    pass_rate: float = 0.0
    estimated_pass_rate_pow_k: dict[str, float] = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)
    cost: VarianceStats = Field(default_factory=VarianceStats)
    duration: VarianceStats = Field(default_factory=VarianceStats)
    assertion_stats: list[AssertionStat] = Field(default_factory=list)
    high_variance: bool = False
    per_run_results: list[Result] = Field(default_factory=list)


class RunManifest(BaseModel):
    """Manifest for a complete eval run."""

    run_id: str
    timestamp: datetime
    kind: RunKind = RunKind.EVAL
    scenarios: dict[str, list[ScenarioRun]] = Field(default_factory=dict)
    command: list[str] | None = None
    captured_input: str | None = None
    trace_path: str | None = None
    exit_code: int | None = None
    duration_seconds: float | None = None
    stdout: str | None = None
    stderr: str | None = None
    span_count: int | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> RunManifest:
        if self.kind == RunKind.CAPTURE:
            if self.scenarios:
                raise ValueError("capture manifests must not contain scenarios")
            if not self.command:
                raise ValueError("capture manifests require a command")
            if self.exit_code is None:
                raise ValueError("capture manifests require an exit_code")
            if self.duration_seconds is None:
                raise ValueError("capture manifests require a duration_seconds value")
        return self

    @property
    def is_capture(self) -> bool:
        return self.kind == RunKind.CAPTURE

    @property
    def is_eval(self) -> bool:
        return self.kind == RunKind.EVAL

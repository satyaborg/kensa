"""OpenInference to kensa span translation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, cast

from kensa import pricing
from kensa.models import CostInfo, Span, SpanKind, TokenCounts, ToolInfo

_OI_KIND_MAP: dict[str, SpanKind] = {
    "LLM": SpanKind.LLM,
    "TOOL": SpanKind.TOOL,
    "AGENT": SpanKind.AGENT,
    "CHAIN": SpanKind.CHAIN,
    "RETRIEVER": SpanKind.RETRIEVER,
    "EVALUATOR": SpanKind.EVALUATOR,
}

_KENSA_TO_OI_KIND: dict[SpanKind, str] = {v: k for k, v in _OI_KIND_MAP.items()}


def _fetch_openrouter_prices() -> dict[str, dict[str, float]]:
    """Compatibility wrapper for pricing fetches used in tests."""
    return pricing.fetch_openrouter_prices()


def _compute_cost(
    model: str | None,
    tokens: TokenCounts | None,
) -> CostInfo | None:
    """Compatibility wrapper for pricing-based cost backfill."""
    return pricing.compute_cost(model, tokens)


def _get(attrs: dict[str, Any], key: str, default: Any = None) -> Any:
    """Get a value from a flat or nested attribute dict."""
    if key in attrs:
        return attrs[key]
    parts = key.split(".")
    current: Any = attrs
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _parse_timestamp(value: Any) -> datetime:
    """Parse a timestamp from various formats."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        numeric = float(value)
        abs_value = abs(numeric)
        if abs_value >= 1e18:
            numeric /= 1e9
        elif abs_value >= 1e15:
            numeric /= 1e6
        elif abs_value >= 1e12:
            numeric /= 1e3
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError(f"Cannot parse timestamp: {value!r}")


def _extract_kind(attrs: dict[str, Any]) -> SpanKind:
    raw_kind = _get(attrs, "openinference.span.kind", "CHAIN")
    return _OI_KIND_MAP.get(str(raw_kind).upper(), SpanKind.CHAIN)


def _extract_tokens(attrs: dict[str, Any]) -> TokenCounts | None:
    prompt_tokens = _get(attrs, "llm.token_count.prompt", 0)
    completion_tokens = _get(attrs, "llm.token_count.completion", 0)
    total_tokens = _get(attrs, "llm.token_count.total", 0)
    cache_read_tokens = _get(attrs, "llm.token_count.cache_read", 0)
    if not (prompt_tokens or completion_tokens or total_tokens):
        return None
    return TokenCounts(
        prompt=int(prompt_tokens),
        completion=int(completion_tokens),
        total=int(total_tokens) or int(prompt_tokens) + int(completion_tokens),
        cache_read=int(cache_read_tokens),
    )


def _extract_declared_cost(attrs: dict[str, Any]) -> CostInfo | None:
    cost_total = _get(attrs, "llm.cost.total", 0.0)
    cost_prompt = _get(attrs, "llm.cost.prompt", 0.0)
    cost_completion = _get(attrs, "llm.cost.completion", 0.0)
    if not (cost_total or cost_prompt or cost_completion):
        return None
    return CostInfo(
        prompt=float(cost_prompt),
        completion=float(cost_completion),
        total=float(cost_total) or float(cost_prompt) + float(cost_completion),
    )


def _parse_tool_args(raw_params: Any) -> dict[str, Any]:
    if isinstance(raw_params, dict):
        return cast(dict[str, Any], raw_params)
    if not raw_params:
        return {}
    if isinstance(raw_params, str):
        try:
            parsed = json.loads(raw_params)
            if isinstance(parsed, dict):
                return cast(dict[str, Any], parsed)
        except (json.JSONDecodeError, TypeError):
            pass
    return {"raw": raw_params}


def _extract_direct_tool(attrs: dict[str, Any]) -> list[ToolInfo]:
    tool_name = _get(attrs, "tool.name")
    if not tool_name:
        return []
    return [
        ToolInfo(
            name=str(tool_name),
            args=_parse_tool_args(_get(attrs, "tool.parameters", {}) or {}),
        )
    ]


def _extract_embedded_tools(attrs: dict[str, Any]) -> list[ToolInfo]:
    tools: list[ToolInfo] = []
    for key, value in attrs.items():
        if not key.endswith(".tool_call.function.name"):
            continue
        if not key.startswith(("llm.output_messages.", "llm.tool_calls.")):
            continue
        args_key = key.replace(".function.name", ".function.arguments")
        tools.append(ToolInfo(name=str(value), args=_parse_tool_args(attrs.get(args_key, "{}"))))
    return tools


def _extract_tools(attrs: dict[str, Any]) -> list[ToolInfo]:
    direct_tools = _extract_direct_tool(attrs)
    if direct_tools:
        return direct_tools
    return _extract_embedded_tools(attrs)


def _extract_io_payload(
    messages: Any,
    value: Any,
) -> dict[str, Any] | None:
    if messages is not None:
        return {"messages": messages}
    if value is None:
        return None
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    return {"value": value}


def _extract_status(oi_span: dict[str, Any]) -> str:
    status_obj: Any = oi_span.get("status", {})
    status_code: str
    if isinstance(status_obj, dict):
        status_code = str(cast(dict[str, Any], status_obj).get("status_code", "OK"))
    elif isinstance(status_obj, str):
        status_code = status_obj
    else:
        status_code = "OK"
    return "error" if status_code.upper() in ("ERROR", "2") else "ok"


def _extract_metadata(attrs: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in attrs.items()
        if not k.startswith(("llm.", "openinference.", "input.", "output.", "tool."))
    }


def oi_to_kensa(oi_span: dict[str, Any]) -> Span:
    """Convert an OpenInference span dict to an kensa Span."""
    attrs = oi_span.get("attributes", {})
    kind = _extract_kind(attrs)
    tokens = _extract_tokens(attrs)
    model_name = _get(attrs, "llm.model_name")
    cost = _extract_declared_cost(attrs) or _compute_cost(model_name, tokens)

    return Span(
        trace_id=str(oi_span.get("trace_id", "")),
        span_id=str(oi_span.get("span_id", "")),
        parent_span_id=oi_span.get("parent_span_id"),
        name=oi_span.get("name", ""),
        kind=kind,
        start_time=_parse_timestamp(oi_span.get("start_time", 0)),
        end_time=_parse_timestamp(oi_span.get("end_time", 0)),
        status=_extract_status(oi_span),
        model=model_name,
        provider=_get(attrs, "llm.provider"),
        input=_extract_io_payload(_get(attrs, "llm.input_messages"), _get(attrs, "input.value")),
        output=_extract_io_payload(_get(attrs, "llm.output_messages"), _get(attrs, "output.value")),
        tokens=tokens,
        cost=cost,
        tools=_extract_tools(attrs),
        metadata=_extract_metadata(attrs),
    )


def kensa_to_oi(span: Span) -> dict[str, Any]:
    """Convert an kensa Span to an OpenInference-style dict."""
    attrs: dict[str, Any] = {}

    attrs["openinference.span.kind"] = _KENSA_TO_OI_KIND.get(span.kind, "CHAIN")

    if span.model:
        attrs["llm.model_name"] = span.model
    if span.provider:
        attrs["llm.provider"] = span.provider

    if span.tokens:
        attrs["llm.token_count.prompt"] = span.tokens.prompt
        attrs["llm.token_count.completion"] = span.tokens.completion
        attrs["llm.token_count.total"] = span.tokens.total
        if span.tokens.cache_read:
            attrs["llm.token_count.cache_read"] = span.tokens.cache_read

    if span.cost:
        attrs["llm.cost.prompt"] = span.cost.prompt
        attrs["llm.cost.completion"] = span.cost.completion
        attrs["llm.cost.total"] = span.cost.total

    if span.tools:
        if len(span.tools) == 1:
            attrs["tool.name"] = span.tools[0].name
            attrs["tool.parameters"] = span.tools[0].args
        else:
            for i, tool in enumerate(span.tools):
                attrs[f"llm.tool_calls.{i}.tool_call.function.name"] = tool.name
                attrs[f"llm.tool_calls.{i}.tool_call.function.arguments"] = json.dumps(
                    tool.args,
                    sort_keys=True,
                )

    if span.input:
        if "messages" in span.input:
            attrs["llm.input_messages"] = span.input["messages"]
        elif "value" in span.input:
            attrs["input.value"] = span.input["value"]
        else:
            attrs["input.value"] = json.dumps(span.input)

    if span.output:
        if "messages" in span.output:
            attrs["llm.output_messages"] = span.output["messages"]
        elif "value" in span.output:
            attrs["output.value"] = span.output["value"]
        else:
            attrs["output.value"] = json.dumps(span.output)

    attrs.update(span.metadata)

    return {
        "trace_id": span.trace_id,
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "name": span.name,
        "start_time": span.start_time.isoformat(),
        "end_time": span.end_time.isoformat(),
        "status": {"status_code": "ERROR" if span.status == "error" else "OK"},
        "attributes": attrs,
    }

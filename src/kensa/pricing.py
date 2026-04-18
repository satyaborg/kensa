"""Model pricing from OpenRouter."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from kensa.models import CostInfo, TokenCounts

_OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
_OPENROUTER_TIMEOUT = 2

_MODEL_PRICES: dict[str, dict[str, float]] | None = None

_DATE_SUFFIX_RE = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})$")
_VERSION_DASH_RE = re.compile(r"(?<!\d)(\d)-(\d{1,2})(?!\d)")


def fetch_openrouter_prices() -> dict[str, dict[str, float]]:
    with urllib.request.urlopen(_OPENROUTER_URL, timeout=_OPENROUTER_TIMEOUT) as resp:
        data = json.loads(resp.read())
    prices: dict[str, dict[str, float]] = {}
    for model in data.get("data", []):
        model_id: str = model.get("id", "")
        pricing = model.get("pricing", {})
        prompt = pricing.get("prompt")
        completion = pricing.get("completion")
        if prompt is None or completion is None:
            continue
        short_id = model_id.split("/", 1)[-1] if "/" in model_id else model_id
        entry: dict[str, float] = {
            "input_cost_per_token": float(prompt),
            "output_cost_per_token": float(completion),
        }
        cache_read = pricing.get("input_cache_read")
        if cache_read is not None:
            entry["cache_read_input_token_cost"] = float(cache_read)
        prices[short_id] = entry
    return prices


def get_model_prices() -> dict[str, dict[str, float]]:
    global _MODEL_PRICES
    if _MODEL_PRICES is None:
        try:
            _MODEL_PRICES = fetch_openrouter_prices()
        except (
            OSError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            urllib.error.URLError,
        ):
            _MODEL_PRICES = {}
    return _MODEL_PRICES


def candidate_slugs(model: str) -> list[str]:
    if "/" in model:
        model = model.split("/", 1)[-1]

    seen: list[str] = []

    def add(name: str) -> None:
        if name and name not in seen:
            seen.append(name)

    add(model)

    date_match = _DATE_SUFFIX_RE.search(model)
    if date_match:
        date_suffix = date_match.group(0)
        base = model[: date_match.start()]
    else:
        date_suffix = ""
        base = model

    dotted_base = _VERSION_DASH_RE.sub(r"\1.\2", base)
    dashed_base = base.replace(".", "-")

    if date_suffix:
        add(dotted_base + date_suffix)
        add(dashed_base + date_suffix)
    add(dotted_base)
    add(base)
    add(dashed_base)

    return seen


def compute_cost(model: str | None, tokens: TokenCounts | None) -> CostInfo | None:
    if model is None or tokens is None:
        return None
    prices = get_model_prices()
    pricing: dict[str, float] | None = None
    for candidate in candidate_slugs(model):
        pricing = prices.get(candidate)
        if pricing is not None:
            break
    if pricing is None:
        return None

    input_rate = pricing["input_cost_per_token"]
    output_rate = pricing["output_cost_per_token"]
    cache_read_rate = pricing.get("cache_read_input_token_cost", input_rate)

    non_cached_prompt = max(0, tokens.prompt - tokens.cache_read)
    input_cost = non_cached_prompt * input_rate + tokens.cache_read * cache_read_rate
    output_cost = tokens.completion * output_rate
    return CostInfo(
        prompt=input_cost,
        completion=output_cost,
        total=input_cost + output_cost,
    )

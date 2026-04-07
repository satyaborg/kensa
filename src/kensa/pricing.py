"""Model pricing helpers used for optional cost backfill."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from kensa.models import CostInfo, TokenCounts

_OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
_OPENROUTER_TIMEOUT = 2

_MODEL_PRICES: dict[str, dict[str, float]] | None = None


def fetch_openrouter_prices() -> dict[str, dict[str, float]]:
    """Fetch live model prices from OpenRouter (free, no auth)."""
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
        dashed = short_id.replace(".", "-")
        if dashed != short_id:
            prices[dashed] = entry
    return prices


def get_model_prices() -> dict[str, dict[str, float]]:
    """Lazy-load prices from OpenRouter.

    Pricing is optional enrichment. If the upstream lookup fails, cache an empty
    mapping so trace translation can continue without cost backfill.
    """
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


def compute_cost(
    model: str | None,
    tokens: TokenCounts | None,
) -> CostInfo | None:
    """Compute cost from tokens and model pricing. Returns None if unknown."""
    if model is None or tokens is None:
        return None
    prices = get_model_prices()
    pricing = prices.get(model)
    if pricing is None:
        normalized = re.sub(r"-\d{8,}$", "", model)
        pricing = prices.get(normalized)
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

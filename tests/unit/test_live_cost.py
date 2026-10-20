"""Live pricing augments, rather than replaces, consolidated cost estimates."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from ark_mcp.domain.pricing import ChargeItem, ModelPricing
from ark_mcp.providers.modelark.pricing import get_price_book
from ark_mcp.tools._cost import estimate_cost, log_cost_estimate, price_source


def _populate(*items: tuple[str, float, str], max_age: float = 60) -> None:
    get_price_book().update(
        {
            "model": ModelPricing(
                catalog_name="model",
                display_name="Model",
                available=True,
                charge_items=[
                    ChargeItem(charge_type=kind, price=price, list_price=price, unit=unit)
                    for kind, price, unit in items
                ],
            )
        },
        max_age_seconds=max_age,
    )


def test_live_image_prices_keep_fractional_cents() -> None:
    _populate(("ToICompletion", 0.045, "Piece"))
    assert estimate_cost(product="image", variations=1, model_id="model-260101") == 0.045
    assert estimate_cost(product="image", variations=4, model_id="model-260101") == 0.18
    assert price_source("image", "model-260101") == "live"
    with patch("ark_mcp.tools._cost.log_info") as log:
        log_cost_estimate(product="image", variations=1, model_id="model-260101")
    assert log.call_args.kwargs["price_source"] == "live"
    assert log.call_args.kwargs["estimated_cost_usd"] == 0.045


@pytest.mark.parametrize("model_id", [None, "unknown"])
def test_unknown_models_use_builtin_prices(model_id: str | None) -> None:
    _populate(("ToICompletion", 0.045, "Piece"))
    assert estimate_cost(product="image", variations=1, model_id=model_id) == 0.03
    assert price_source("image", model_id) == "builtin"


def test_stale_snapshot_falls_back_to_builtin_prices() -> None:
    _populate(("ToICompletion", 0.045, "Piece"), max_age=-1)
    assert estimate_cost(product="image", variations=1, model_id="model") == 0.03


def test_unrecognized_unit_does_not_change_estimate() -> None:
    _populate(("ToICompletion", 0.045, "K tokens"))
    assert estimate_cost(product="image", variations=1, model_id="model") == 0.03


def test_free_image_rate_is_not_replaced_by_builtin_price() -> None:
    _populate(("ToICompletion", 0, "Piece"))
    assert estimate_cost(product="image", variations=1, model_id="model") == 0


def test_understanding_converts_both_token_rates() -> None:
    _populate(("InferencePrompt", 0.0004, "K tokens"), ("InferenceCompletion", 0.002, "K tokens"))
    assert estimate_cost(
        product="understanding",
        variations=1,
        prompt_tokens=1_000_000,
        max_tokens=1_000_000,
        model_id="model",
    ) == pytest.approx(2.4)


def test_partial_token_rates_fall_back_together() -> None:
    _populate(("InferencePrompt", 0.0004, "K tokens"))
    assert (
        estimate_cost(product="understanding", variations=1, max_tokens=1_000_000, model_id="model")
        == 3
    )


def test_video_cost_still_uses_bound_family() -> None:
    _populate(("V2VCompletion", 9, "K tokens"))
    assert (
        estimate_cost(product="video", variations=1, model_id="model", video_family="seedance_2_5")
        == 0.35
    )


@pytest.mark.parametrize("price", [-1, float("nan"), float("inf")])
def test_invalid_catalog_prices_are_rejected(price: float) -> None:
    with pytest.raises(ValidationError):
        ChargeItem(charge_type="ToICompletion", price=price, list_price=1, unit="Piece")

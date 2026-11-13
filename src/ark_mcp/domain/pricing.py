"""Model pricing domain models, sourced from the ModelArk pricing catalog.

These models appear in MCP tool output schemas, so every field is described.
Prices are the account's final unit prices as returned by the catalog action
``ListModelChargeItems`` (contract, promotion, and plan discounts applied).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

_PER_THOUSAND_TOKENS_UNITS = frozenset({"k tokens", "千tokens"})
_PER_PIECE_UNITS = frozenset({"piece"})


class ChargeItem(BaseModel):
    """Account-specific catalog rate."""

    charge_type: str = Field(
        ...,
        description="Catalog rate type, e.g. InferencePrompt, InferenceCompletion, or ToICompletion.",
    )
    price: float = Field(
        ...,
        ge=0,
        allow_inf_nan=False,
        description="Discounted account price per unit.",
    )
    list_price: float = Field(
        ...,
        ge=0,
        allow_inf_nan=False,
        description="Published price per unit before discounts.",
    )
    unit: str = Field(
        ...,
        description="Catalog billing unit: K tokens (1,000 tokens), Piece, or another unit.",
    )

    @property
    def price_per_million_tokens(self) -> float | None:
        """Price per 1,000,000 tokens, or None when this rate is not billed per K tokens."""
        if self.unit.strip().lower() not in _PER_THOUSAND_TOKENS_UNITS:
            return None
        return self.price * 1000

    @property
    def price_per_piece(self) -> float | None:
        """Price per generated item, or None when this rate is not billed per piece."""
        if self.unit.strip().lower() not in _PER_PIECE_UNITS:
            return None
        return self.price


class ModelPricing(BaseModel):
    """Cached foundation-model prices."""

    catalog_name: str = Field(
        ...,
        description="Foundation model name without a trailing version date.",
    )
    display_name: str = Field(..., description="Human-readable model name from the catalog.")
    available: bool = Field(
        ...,
        description="Whether this account has activated the model.",
    )
    charge_items: list[ChargeItem] = Field(
        default_factory=list,
        description="Catalog rates for this model.",
    )

    def charge_item(self, *charge_types: str) -> ChargeItem | None:
        """Return the first rate matching one of ``charge_types``, in priority order."""
        by_type = {item.charge_type: item for item in self.charge_items}
        for charge_type in charge_types:
            if charge_type in by_type:
                return by_type[charge_type]
        return None


PricingStatus = Literal["live", "unavailable"]

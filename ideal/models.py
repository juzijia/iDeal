from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any


def decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


@dataclass(slots=True)
class Claim:
    source: str
    source_id: str
    app_id: str
    region: str
    title: str
    url: str
    published_at: str = ""
    deal_kind: str = "discovery"
    old_price: Decimal | None = None
    new_price: Decimal | None = None
    currency: str = ""
    description: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def claim_key(self) -> str:
        raw = "|".join(
            [
                self.source,
                self.source_id,
                self.app_id,
                self.region,
                self.published_at,
                decimal_text(self.old_price) or "",
                decimal_text(self.new_price) or "",
                self.deal_kind,
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def as_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["old_price"] = decimal_text(self.old_price)
        data["new_price"] = decimal_text(self.new_price)
        return data


@dataclass(slots=True)
class OfficialApp:
    app_id: str
    region: str
    title: str
    price: Decimal
    currency: str
    formatted_price: str
    url: str
    primary_genre_id: str = ""
    primary_genre_name: str = ""
    seller: str = ""
    version: str = ""
    release_notes: str = ""
    updated_at: str = ""
    minimum_os: str = ""
    rating: float = 0.0
    rating_count: int = 0
    artwork_url: str = ""
    description: str = ""
    content_rating: str = ""
    languages: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Transition:
    app_id: str
    region: str
    previous_price: Decimal | None
    current_price: Decimal
    observed_min_before: Decimal | None
    observation_count_before: int
    cycle: int
    changed: bool

    @property
    def is_drop(self) -> bool:
        return self.previous_price is not None and self.current_price < self.previous_price

    @property
    def is_new_low(self) -> bool:
        return (
            self.observation_count_before >= 2
            and self.observed_min_before is not None
            and self.current_price <= self.observed_min_before
        )


@dataclass(slots=True)
class DealEvent:
    event_key: str
    app: OfficialApp
    old_price: Decimal
    new_price: Decimal
    alert_type: str
    confidence: str
    score: int
    evidence: list[Claim] = field(default_factory=list)
    historical_low: bool = False
    tags: list[str] = field(default_factory=list)
    selection_reason: str = ""

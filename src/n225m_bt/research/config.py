"""Small, explicit preregistered experiment configuration."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from n225m_bt.config import StrictModel

Family = Literal["opening_momentum", "opening_reversal", "opening_breakout"]


class CampaignConfig(StrictModel):
    schema_version: Literal[1]
    families: tuple[Family, ...]
    lookback_minutes: tuple[Annotated[int, Field(ge=2, le=60)], ...]
    holding_minutes: tuple[Annotated[int, Field(ge=2, le=120)], ...]
    representative_lookback: int
    representative_holding: int
    seed: int
    resamples: Annotated[int, Field(ge=100, le=10000)]
    minimum_development_trades: Annotated[int, Field(ge=1)]
    minimum_oos_trades: Annotated[int, Field(ge=1)]
    minimum_positive_neighbors: Annotated[int, Field(ge=1)]
    hypothesis_document: Path = Path("docs/strategy/03_experiment_plan.md")

    @model_validator(mode="after")
    def bounded_unique_grid(self) -> "CampaignConfig":
        for values in (self.families, self.lookback_minutes, self.holding_minutes):
            if not values or len(set(values)) != len(values):
                raise ValueError("grid dimensions must be nonempty and unique")
        points = len(self.lookback_minutes) * len(self.holding_minutes)
        if points * len(self.families) > 36:
            raise ValueError("preregister a smaller grid (at most 36 combinations)")
        if self.minimum_positive_neighbors > points:
            raise ValueError("positive-neighbor threshold exceeds grid size")
        if (
            self.representative_lookback not in self.lookback_minutes
            or self.representative_holding not in self.holding_minutes
        ):
            raise ValueError("representative must be in the grid")
        return self

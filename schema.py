"""Structured output for the sharks.

Each shark returns EXACTLY this shape, so the UI can render one clean card per
shark instead of a wall of prose.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Verdict(BaseModel):
    shark: str = Field(description="the shark's name")
    stance: Literal["INVEST", "PASS"] = Field(description="INVEST or PASS")
    offer: str | None = Field(
        default=None, description="an offer like 'Rs 50L for 10%', or null if passing"
    )
    reasoning: str = Field(
        description="2-3 sentences justifying the stance, in the shark's own voice"
    )

    @model_validator(mode="after")
    def offer_requires_invest(self) -> Verdict:
        if self.stance == "PASS" and self.offer is not None:
            raise ValueError("A passing verdict cannot include an offer.")
        return self

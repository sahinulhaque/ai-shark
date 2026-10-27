"""Structured output for the sharks.

Each shark returns EXACTLY this shape, so the UI can render one clean card per
shark instead of a wall of prose.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Verdict(BaseModel):
    shark: str = Field(description="the shark's name")
    stance: str = Field(description="INVEST or PASS")
    offer: Optional[str] = Field(
        default=None, description="an offer like 'Rs 50L for 10%', or null if passing")
    reasoning: str = Field(
        description="2-3 sentences justifying the stance, in the shark's own voice")

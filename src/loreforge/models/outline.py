"""Outline: the section plan a script is written against."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class GameFlavor(BaseModel):
    """Per-game voice for the ritual parts (opening, wind-down, description)."""
    address: str = "traveler"          # how the listener is addressed: "Hunter", "Tarnished"
    welcome_image: str = ""            # the safe place the opening builds (a lamp-lit clinic...)
    farewell: str = ""                 # the last line: "Good night, Hunter..."
    fans_of: list[str] = Field(default_factory=list)   # similar games, for the description


class Section(BaseModel):
    id: str                            # s01, s02...
    kind: Literal["opening", "body", "winddown"] = "body"
    title: str
    goal: str = ""                     # what this section must accomplish, in one or two lines
    target_words: int = 1000
    fact_ids: list[str] = Field(default_factory=list)
    visual_tags: list[str] = Field(default_factory=list)   # "cathedral", "moon", "streets"...
    midroll: bool = False              # the one break where the narrator asks for a subscribe


class Outline(BaseModel):
    game: str
    format: str
    working_title: str = ""
    opening_mode: str = ""             # which entrance the opening uses; see templates/opening.md
    flavor: GameFlavor = Field(default_factory=GameFlavor)
    sections: list[Section] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def total_words(self) -> int:
        return sum(s.target_words for s in self.sections)

    def section(self, section_id: str) -> Section | None:
        return next((s for s in self.sections if s.id == section_id), None)

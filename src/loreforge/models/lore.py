"""Lore bible: the per-game fact base every outline and script is grounded in."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CATEGORIES = [
    "world", "history", "faction", "character", "location", "item", "weapon",
    "boss", "enemy", "ending", "mechanic", "production", "secret", "theme",
]

Status = Literal["canon", "inferred", "theory"]


class SourceRef(BaseModel):
    src_id: str
    locator: str = ""


class Entity(BaseModel):
    id: str
    name: str
    kind: str = "concept"
    aliases: list[str] = Field(default_factory=list)


class Fact(BaseModel):
    id: str
    text: str
    entity_ids: list[str] = Field(default_factory=list)
    category: str = "world"
    status: Status = "canon"
    chrono: int | None = None          # 0 = deep past .. 100 = the ending; None = timeless
    sources: list[SourceRef] = Field(default_factory=list)


class Event(BaseModel):
    id: str
    order: int
    summary: str
    fact_ids: list[str] = Field(default_factory=list)


class LoreBible(BaseModel):
    game: str
    updated_at: str = ""
    sources: dict[str, dict] = Field(default_factory=dict)   # src_id -> {kind, title, origin, sha}
    entities: list[Entity] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)

    def fact(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fact_id), None)

    def fact_map(self) -> dict[str, Fact]:
        return {f.id: f for f in self.facts}

    def entity_names(self) -> set[str]:
        names: set[str] = set()
        for e in self.entities:
            names.add(e.name)
            names.update(e.aliases)
        return names

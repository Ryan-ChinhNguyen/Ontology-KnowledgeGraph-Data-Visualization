"""The shape every parser produces, and the parser interface itself.

Downstream stages (ontology proposal, graph building) read only
``NormalizedData``, so they are unaffected by which format the data arrived in.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Column:
    name: str
    inferred_type: str


@dataclass(frozen=True)
class Relationship:
    """A link between two tables. Populated from foreign keys where the source
    declares them; inferred later for formats that do not."""

    from_table: str
    to_table: str
    type: str


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)


@dataclass
class NormalizedData:
    tables: list[Table] = field(default_factory=list)


class BaseParser(ABC):
    @abstractmethod
    def parse(self, file_paths: list[str]) -> NormalizedData:
        """Read the given files into tables.

        Raises on unreadable input; the caller records the error against the
        job and decides whether to retry.
        """

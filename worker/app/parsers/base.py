"""The shape every parser produces, and the parser interface itself.

Downstream stages (ontology proposal, graph building) read only
``NormalizedData``, so they are unaffected by which format the data arrived in.

Rows are carried as a DataFrame rather than as a list of dictionaries. A
dictionary per row costs about nine times the size of the file it came from,
where a DataFrame costs about four, and the DataFrame is what gets written to
Parquet — so the dictionaries would be built only to be thrown away.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class Column:
    name: str
    inferred_type: str


@dataclass(frozen=True)
class Relationship:
    """A link between two tables. Populated from foreign keys where the source
    declares them; inferred later for formats that do not.

    The columns are what make the link usable: without them a relationship says
    two tables are connected but not how, which is not enough to follow an edge
    from one row to another. A composite key is joined into one string rather
    than becoming several relationships, because it is one link, not many.
    """

    from_table: str
    to_table: str
    type: str
    from_column: str = ""
    to_column: str = ""


@dataclass
class Table:
    """A parsed table.

    ``columns`` is kept alongside the frame rather than derived from it: a SQL
    dump declares its own types, which say more than the types pandas infers
    from the values.
    """

    name: str
    columns: list[Column] = field(default_factory=list)
    frame: pd.DataFrame = field(default_factory=pd.DataFrame)
    relationships: list[Relationship] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.frame)


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

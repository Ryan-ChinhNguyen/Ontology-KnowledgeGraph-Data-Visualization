from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Column:
    name: str
    inferred_type: str


@dataclass
class Relationship:
    from_table: str
    to_table: str
    type: str


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)


@dataclass
class NormalizedData:
    tables: list[Table] = field(default_factory=list)


class BaseParser(ABC):
    @abstractmethod
    def parse(self, file_paths: list[str]) -> NormalizedData:
        pass

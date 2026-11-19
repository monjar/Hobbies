from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Span:
    """A [start, end) stretch of time in seconds."""

    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return asdict(self)

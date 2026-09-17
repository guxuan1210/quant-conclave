"""Immutable, serializable evidence snapshots shared by analysis agents."""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class EvidenceStatus(str, Enum):
    AVAILABLE = "available"
    NO_DATA = "no_data"
    DEGRADED = "degraded"
    STALE = "stale"
    ERROR = "error"


@dataclass(frozen=True)
class EvidenceItem:
    kind: str
    source: str
    as_of: str
    fetched_at: str
    status: EvidenceStatus
    payload: dict[str, Any]
    period_end: str = ""
    filed_at: str = ""
    freshness: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "status": self.status.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceItem":
        values = dict(data)
        values["status"] = EvidenceStatus(values["status"])
        return cls(**values)


@dataclass(frozen=True)
class EvidencePack:
    symbol: str
    analysis_date: str
    items: tuple[EvidenceItem, ...]

    @property
    def quality(self) -> str:
        statuses = {item.status for item in self.items}
        if EvidenceStatus.ERROR in statuses or EvidenceStatus.DEGRADED in statuses:
            return "degraded"
        if EvidenceStatus.STALE in statuses:
            return "stale"
        return "complete"

    def get(self, kind: str) -> EvidenceItem | None:
        return next((item for item in self.items if item.kind == kind), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "analysis_date": self.analysis_date,
            "quality": self.quality,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidencePack":
        return cls(
            symbol=data["symbol"],
            analysis_date=data["analysis_date"],
            items=tuple(EvidenceItem.from_dict(item) for item in data.get("items", [])),
        )

    def to_prompt_summary(self, max_payload_chars: int = 1200) -> str:
        lines = [f"Evidence snapshot for {self.symbol} as of {self.analysis_date}:"]
        for item in self.items:
            payload = str(item.payload)[:max_payload_chars]
            lines.append(
                f"- {item.kind}: {item.status.value.upper()} | source={item.source} "
                f"| as_of={item.as_of or 'unknown'} | filed_at={item.filed_at or 'n/a'} "
                f"| data={payload}"
            )
        return "\n".join(lines)

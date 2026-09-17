"""Immutable, serializable evidence snapshots shared by analysis agents."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import base64
import copy
from collections.abc import Mapping
from typing import Any


class EvidenceStatus(str, Enum):
    AVAILABLE = "available"
    NO_DATA = "no_data"
    DEGRADED = "degraded"
    STALE = "stale"
    ERROR = "error"


def _json_safe(value: Any, *, path: str = "payload") -> Any:
    """Convert common analytical values to JSON-compatible values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return _json_safe(value.value, path=path)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, Mapping):
        result = {}
        for key, nested in value.items():
            if not isinstance(key, (str, int, float, bool)) and not isinstance(key, Enum):
                raise TypeError(f"{path}: mapping key {key!r} is not JSON serializable")
            result[str(key) if not isinstance(key, str) else key] = _json_safe(
                nested, path=f"{path}[{key!r}]"
            )
        return result
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item, path=f"{path}[set]") for item in sorted(value, key=repr)]
    # numpy scalar values expose item() and are converted without making numpy a dependency.
    if value.__class__.__module__.split(".", 1)[0] == "numpy" and hasattr(value, "item"):
        return _json_safe(value.item(), path=path)
    raise TypeError(f"{path}: value of type {type(value).__name__} is not JSON serializable")


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", copy.deepcopy(self.payload))

    def to_dict(self) -> dict[str, Any]:
        result = {**self.__dict__, "status": self.status.value}
        result["payload"] = _json_safe(result["payload"])
        return copy.deepcopy(result)

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

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))

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

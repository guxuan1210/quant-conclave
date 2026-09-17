from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class Market(str, Enum):
    CN = "CN"
    US = "US"
    HK = "HK"
    JP = "JP"
    IN = "IN"
    GB = "GB"
    CA = "CA"
    AU = "AU"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class InstrumentProfile:
    symbol: str
    asset_type: str
    market: Market
    exchange: str
    currency: str
    timezone: str
    calendar: str
    benchmark: str
    sector_taxonomy: str
    capabilities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["market"] = self.market.value
        data["capabilities"] = list(self.capabilities)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InstrumentProfile":
        values = dict(data)
        values["market"] = Market(values["market"])
        values["capabilities"] = tuple(values.get("capabilities", ()))
        return cls(**values)

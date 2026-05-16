from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    action: str
    symbol: str
    direction: int
    leverage: float
    priority: int
    name: str = ""


@dataclass(frozen=True)
class ProbeResult:
    provider: str
    symbol: str
    provider_symbol: str
    ok: bool
    message: str


ACTION_ID_TO_NAME = {
    0: "flat",
    1: "long_1x",
    2: "long_2x",
    3: "short_1x",
    4: "short_2x",
}

ACTION_NAME_TO_ID = {value: key for key, value in ACTION_ID_TO_NAME.items()}

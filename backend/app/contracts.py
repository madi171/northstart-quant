from typing import Protocol
from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class MarketDataProvider(Protocol):
    def bars(self, symbol: str, start: str, end: str) -> list[Bar]: ...


class NewsProvider(Protocol):
    def news(self, symbols: list[str], as_of: str) -> list[dict]: ...


class ModelProvider(Protocol):
    def propose(self, context: dict) -> dict: ...


class BrokerAdapter(Protocol):
    def balances(self) -> dict: ...
    def positions(self) -> list[dict]: ...
    def submit(self, intent: dict, idempotency_key: str) -> dict: ...
    def order(self, broker_order_id: str) -> dict: ...
    def cancel(self, broker_order_id: str) -> dict: ...

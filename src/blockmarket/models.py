"""Small immutable value types shared by the pure core."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .rules import MONEY_TICK, money


@dataclass(frozen=True)
class Account:
    cash: Decimal
    inventory: int
    fees: Decimal = Decimal(0)

    @property
    def net_cash(self) -> Decimal:
        return self.cash - self.fees

    def equity(self, mark_price: Decimal) -> Decimal:
        return self.cash + Decimal(self.inventory) * mark_price - self.fees

    def to_json(self, mark_price: Decimal) -> dict[str, Any]:
        return {
            "cash": money(self.cash),
            "inventory": str(self.inventory),
            "fees": money(self.fees),
            "equity": money(self.equity(mark_price)),
        }


@dataclass(frozen=True)
class Quote:
    price: Decimal
    quantity: int


@dataclass(frozen=True)
class CustomerOrder:
    kind: str
    side: str
    quantity: int
    reservation_price: Decimal

    def to_json(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "side": self.side,
            "quantity": str(self.quantity),
            "reservation_price": format(self.reservation_price, ".2f"),
        }


@dataclass(frozen=True)
class EnvironmentState:
    x_prev: Decimal
    x_cur: Decimal

    def to_json(self) -> dict[str, str]:
        return {"x_prev": format(self.x_prev, "f"), "x_cur": format(self.x_cur, "f")}


def account_from_json(value: dict[str, Any]) -> Account:
    return Account(
        cash=Decimal(value["cash"]).quantize(MONEY_TICK),
        inventory=int(value["inventory"]),
        fees=Decimal(value["fees"]).quantize(MONEY_TICK),
    )

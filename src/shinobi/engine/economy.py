"""Economie : prix, missions, paie."""

from __future__ import annotations

from shinobi.canon.models import WorldRules


def mission_pay(rules: WorldRules, rank: str) -> int:
    """Paie typique pour une mission selon le rang."""
    return int(rules.economy.mission_pay_by_rank.get(rank, 0))


def jutsu_scroll_price(rules: WorldRules, rank: str) -> int | None:
    """Prix d'un parchemin de technique selon son rang."""
    val = rules.economy.ryo_to_jutsu_scroll_multiplier_by_rank.get(rank)
    if val is None:
        return None
    return int(val)


def daily_living_cost() -> int:
    """Cout quotidien minimum (nourriture + logement modeste pris au prorata)."""
    return 50 + (3000 // 30)


def can_afford(money: int, price: int) -> bool:
    return money >= price


def format_ryos(amount: int) -> str:
    """Format lisible avec separateurs."""
    return f"{amount:,} ryos".replace(",", " ")

"""Economie : prix, missions, paie, cout de vie."""

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


def apply_inflation(price: int, inflation_factor: float) -> int:
    """Ajuste un prix selon l'inflation courante du monde."""
    if inflation_factor <= 0:
        return price
    return max(1, int(price * inflation_factor))


def cost_of_living_for_period(*, days: int, inflation_factor: float = 1.0) -> int:
    """Cout cumule de subsistance pour N jours, ajuste pour l'inflation."""
    if days <= 0:
        return 0
    return apply_inflation(daily_living_cost() * days, inflation_factor)

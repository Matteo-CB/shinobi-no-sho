"""Calcul des prix pour les breadcrumbs."""

from __future__ import annotations

from shinobi.goals.breadcrumbs import BreadcrumbPrice


def base_money_price(*, value_strategique: float, target_rank_factor: float = 1.0) -> int:
    """Prix de base en ryos pour une info."""
    base = 1000.0 * value_strategique * target_rank_factor
    return int(base)


def price_for_anbu(value_strategique: float) -> BreadcrumbPrice:
    """Anbu demande des faveurs, pas d'argent."""
    return BreadcrumbPrice(
        type="favor",
        description="Une faveur a rendre a l'unite Anbu, sans details immediats.",
        amount=max(1.0, value_strategique),
    )


def price_for_yakuza(value_strategique: float) -> BreadcrumbPrice:
    """Un yakuza demande de l'intimidation politique."""
    return BreadcrumbPrice(
        type="political",
        description="Soutenir une operation d'intimidation contre un rival politique du commanditaire.",
        amount=value_strategique,
    )


def price_for_orochimaru() -> BreadcrumbPrice:
    """Orochimaru demande des sujets d'experience."""
    return BreadcrumbPrice(
        type="moral",
        description="Lui amener un sujet vivant pour ses experiences.",
        amount=1.0,
    )


def price_in_money(value_strategique: float, *, target_rank_factor: float = 1.0) -> BreadcrumbPrice:
    return BreadcrumbPrice(
        type="money",
        description="Paiement direct en ryos a un informateur.",
        amount=base_money_price(
            value_strategique=value_strategique, target_rank_factor=target_rank_factor
        ),
    )


def negotiate_price(
    base_price: BreadcrumbPrice,
    *,
    success_margin: int,
) -> BreadcrumbPrice:
    """Reduit le prix selon une marge de negociation reussie."""
    if base_price.amount is None:
        return base_price
    if success_margin >= 10:
        new_amount = base_price.amount * 0.5
    elif success_margin >= 0:
        new_amount = base_price.amount * 0.8
    elif success_margin >= -5:
        new_amount = base_price.amount * 1.0
    else:
        new_amount = base_price.amount * 1.3
    return base_price.model_copy(update={"amount": new_amount})

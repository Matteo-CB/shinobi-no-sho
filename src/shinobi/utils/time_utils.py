"""Calculs de temps in-game.

Calendrier simplifie : 12 mois de 30 jours, pas d'annees bissextiles.
L'an 1 correspond a la naissance canonique de Naruto.
"""

from __future__ import annotations

from dataclasses import dataclass

DAYS_PER_MONTH = 30
MONTHS_PER_YEAR = 12
DAYS_PER_YEAR = DAYS_PER_MONTH * MONTHS_PER_YEAR
HOURS_PER_DAY = 24
MINUTES_PER_HOUR = 60


@dataclass(frozen=True)
class GameDate:
    """Date in-game. year est signed, an 1 = naissance de Naruto."""

    year: int
    month: int  # 1 a 12
    day: int  # 1 a 30
    hour: int = 0  # 0 a 23
    minute: int = 0  # 0 a 59

    def __post_init__(self) -> None:
        if not 1 <= self.month <= MONTHS_PER_YEAR:
            raise ValueError(f"month doit etre dans [1, {MONTHS_PER_YEAR}]")
        if not 1 <= self.day <= DAYS_PER_MONTH:
            raise ValueError(f"day doit etre dans [1, {DAYS_PER_MONTH}]")
        if not 0 <= self.hour < HOURS_PER_DAY:
            raise ValueError(f"hour doit etre dans [0, {HOURS_PER_DAY})")
        if not 0 <= self.minute < MINUTES_PER_HOUR:
            raise ValueError(f"minute doit etre dans [0, {MINUTES_PER_HOUR})")

    @property
    def date_str(self) -> str:
        """Format MM-DD."""
        return f"{self.month:02d}-{self.day:02d}"

    def add_minutes(self, minutes: int) -> GameDate:
        """Retourne une nouvelle date avancee de N minutes."""
        if minutes < 0:
            raise ValueError("add_minutes doit recevoir un delta positif")
        total_minutes = self.minute + minutes
        new_minute = total_minutes % MINUTES_PER_HOUR
        carry_hours = total_minutes // MINUTES_PER_HOUR
        total_hours = self.hour + carry_hours
        new_hour = total_hours % HOURS_PER_DAY
        carry_days = total_hours // HOURS_PER_DAY
        return self.add_days(carry_days).with_time(new_hour, new_minute)

    def add_hours(self, hours: int) -> GameDate:
        """Retourne une nouvelle date avancee de N heures."""
        return self.add_minutes(hours * MINUTES_PER_HOUR)

    def add_days(self, days: int) -> GameDate:
        """Retourne une nouvelle date avancee de N jours."""
        new_day = self.day + days
        new_month = self.month
        new_year = self.year
        while new_day > DAYS_PER_MONTH:
            new_day -= DAYS_PER_MONTH
            new_month += 1
            if new_month > MONTHS_PER_YEAR:
                new_month = 1
                new_year += 1
        return GameDate(
            year=new_year, month=new_month, day=new_day, hour=self.hour, minute=self.minute
        )

    def with_time(self, hour: int, minute: int) -> GameDate:
        """Retourne une nouvelle date avec heure et minute remplacees."""
        return GameDate(year=self.year, month=self.month, day=self.day, hour=hour, minute=minute)

    def to_total_minutes_since_epoch(self) -> int:
        """Conversion vers un total de minutes depuis l'an 0 (utile pour les diffs)."""
        days = (self.year * DAYS_PER_YEAR) + ((self.month - 1) * DAYS_PER_MONTH) + (self.day - 1)
        return days * HOURS_PER_DAY * MINUTES_PER_HOUR + self.hour * MINUTES_PER_HOUR + self.minute


def compute_age(birth_year: int, current_year: int) -> int:
    """Age en annees pleines. Suppose que le mois de l'anniversaire est passe."""
    return max(0, current_year - birth_year)

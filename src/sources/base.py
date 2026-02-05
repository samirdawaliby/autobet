"""
Base classes for odds sources.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Sport(str, Enum):
    """Supported sports."""
    TENNIS = "tennis"
    SOCCER = "soccer"
    BASKETBALL = "basketball"
    AMERICAN_FOOTBALL = "americanfootball"
    ICE_HOCKEY = "icehockey"
    MMA = "mma"
    BOXING = "boxing"


class Market(str, Enum):
    """Supported betting markets."""
    H2H = "h2h"  # Head to head / Match winner
    SPREADS = "spreads"
    TOTALS = "totals"


@dataclass
class OddsSelection:
    """A single betting selection with odds."""
    name: str  # e.g., "Novak Djokovic", "Home", "Over 2.5"
    odds: float  # Decimal odds
    bookmaker: str
    timestamp: datetime
    liquidity: Optional[float] = None  # For exchanges


@dataclass
class OddsMarket:
    """A betting market with all selections."""
    market_type: Market
    selections: list[OddsSelection] = field(default_factory=list)


@dataclass
class Event:
    """A sporting event with odds from multiple bookmakers."""
    event_id: str
    sport: Sport
    league: str
    home_team: str
    away_team: str
    commence_time: datetime
    markets: dict[str, dict[str, OddsMarket]] = field(default_factory=dict)
    # Structure: {bookmaker: {market_type: OddsMarket}}

    @property
    def display_name(self) -> str:
        return f"{self.home_team} vs {self.away_team}"


@dataclass
class OddsSnapshot:
    """A snapshot of odds from a source."""
    source_name: str
    timestamp: datetime
    events: list[Event] = field(default_factory=list)
    remaining_requests: Optional[int] = None  # API quota


class OddsSource(ABC):
    """Abstract base class for odds data sources."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the source name."""
        pass

    @abstractmethod
    async def fetch_odds(
        self,
        sport: Sport,
        markets: list[Market],
        regions: list[str] | None = None,
        bookmakers: list[str] | None = None,
    ) -> OddsSnapshot:
        """
        Fetch odds for a sport.

        Args:
            sport: The sport to fetch odds for
            markets: List of market types to fetch
            regions: List of regions (e.g., ["eu", "uk", "us"])
            bookmakers: Specific bookmakers to fetch (None = all)

        Returns:
            OddsSnapshot with all events and odds
        """
        pass

    @abstractmethod
    async def get_sports(self) -> list[dict]:
        """Get list of available sports."""
        pass

    async def close(self) -> None:
        """Cleanup resources."""
        pass

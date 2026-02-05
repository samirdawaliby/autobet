"""
Multi-source odds aggregator.
Combines odds from multiple APIs and normalizes them.
"""
import asyncio
import structlog
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .base import Event, Market, OddsMarket, OddsSelection, OddsSnapshot, OddsSource, Sport
from .the_odds_api import TheOddsAPI

logger = structlog.get_logger()


@dataclass
class AggregatedOdds:
    """
    Aggregated odds from all sources for a single event.

    Structure optimized for arbitrage detection:
    - Quick access to best odds per selection
    - All bookmaker odds in one place
    """
    event_id: str
    event_name: str
    sport: Sport
    league: str
    commence_time: datetime
    home_team: str
    away_team: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Odds matrix: {selection_name: {bookmaker: OddsSelection}}
    odds_matrix: dict[str, dict[str, OddsSelection]] = field(default_factory=dict)

    # Precomputed best odds for quick arbitrage check
    best_odds: dict[str, tuple[float, str]] = field(default_factory=dict)
    # {selection_name: (best_odds, best_bookmaker)}

    def add_odds(self, selection_name: str, odds: OddsSelection) -> None:
        """Add odds for a selection from a bookmaker."""
        if selection_name not in self.odds_matrix:
            self.odds_matrix[selection_name] = {}

        self.odds_matrix[selection_name][odds.bookmaker] = odds

        # Update best odds
        current_best = self.best_odds.get(selection_name, (0.0, ""))
        if odds.odds > current_best[0]:
            self.best_odds[selection_name] = (odds.odds, odds.bookmaker)

    def get_bookmaker_count(self) -> int:
        """Get number of unique bookmakers with odds."""
        bookmakers = set()
        for selection_odds in self.odds_matrix.values():
            bookmakers.update(selection_odds.keys())
        return len(bookmakers)


@dataclass
class AggregatedSnapshot:
    """Aggregated odds snapshot from all sources."""
    timestamp: datetime
    events: dict[str, AggregatedOdds] = field(default_factory=dict)
    # {event_id: AggregatedOdds}

    source_stats: dict[str, int] = field(default_factory=dict)
    # {source_name: event_count}

    total_bookmakers: int = 0


class OddsAggregator:
    """
    Aggregates odds from multiple sources.

    Features:
    - Parallel fetching from all sources
    - Event deduplication across sources
    - Odds normalization and timestamp tracking
    - Best odds precomputation for fast arbitrage detection
    """

    # Known exchanges (for commission calculation and execution routing)
    EXCHANGES = {
        "betfair", "betfair_ex_eu", "betfair_ex_uk", "betfair_ex_au",
        "smarkets", "matchbook", "betdaq",
    }

    # Exchange commission rates
    EXCHANGE_COMMISSIONS = {
        "betfair": 0.05,
        "betfair_ex_eu": 0.05,
        "betfair_ex_uk": 0.05,
        "betfair_ex_au": 0.05,
        "smarkets": 0.02,
        "matchbook": 0.02,
        "betdaq": 0.02,
    }

    def __init__(self, sources: list[OddsSource] | None = None):
        self.sources: list[OddsSource] = sources or []

    def add_source(self, source: OddsSource) -> None:
        """Add an odds source."""
        self.sources.append(source)

    @classmethod
    def from_config(cls, config) -> "OddsAggregator":
        """Create aggregator from configuration."""
        aggregator = cls()

        # Add The Odds API if configured
        if config.odds_api.enabled and config.odds_api.api_key:
            aggregator.add_source(
                TheOddsAPI(
                    api_key=config.odds_api.api_key,
                    base_url=config.odds_api.base_url,
                    bookmakers=config.odds_api.bookmakers,
                )
            )
            logger.info("source_added", source="the_odds_api")

        # Add more sources here as they're implemented
        # if config.odds_api_io.enabled:
        #     aggregator.add_source(OddsAPIio(...))

        return aggregator

    async def fetch_all(
        self,
        sports: list[Sport],
        markets: list[Market],
    ) -> AggregatedSnapshot:
        """
        Fetch and aggregate odds from all sources.

        Args:
            sports: Sports to fetch
            markets: Market types to fetch

        Returns:
            AggregatedSnapshot with all odds
        """
        # Fetch from all sources in parallel
        tasks = []
        for source in self.sources:
            for sport in sports:
                tasks.append(self._fetch_from_source(source, sport, markets))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate results
        snapshot = AggregatedSnapshot(timestamp=datetime.now(timezone.utc))

        for result in results:
            if isinstance(result, Exception):
                logger.error("source_fetch_error", error=str(result))
                continue

            if isinstance(result, OddsSnapshot):
                self._merge_snapshot(snapshot, result)

        # Compute statistics
        snapshot.total_bookmakers = self._count_unique_bookmakers(snapshot)

        logger.info(
            "aggregation_complete",
            event_count=len(snapshot.events),
            total_bookmakers=snapshot.total_bookmakers,
            source_stats=snapshot.source_stats,
        )

        return snapshot

    async def _fetch_from_source(
        self,
        source: OddsSource,
        sport: Sport,
        markets: list[Market],
    ) -> OddsSnapshot:
        """Fetch odds from a single source."""
        try:
            return await source.fetch_odds(sport, markets)
        except Exception as e:
            logger.error(
                "source_error",
                source=source.name,
                sport=sport.value,
                error=str(e),
            )
            raise

    def _merge_snapshot(
        self,
        aggregated: AggregatedSnapshot,
        snapshot: OddsSnapshot,
    ) -> None:
        """Merge a source snapshot into the aggregated snapshot."""
        source_event_count = 0

        for event in snapshot.events:
            # Get or create aggregated event
            if event.event_id not in aggregated.events:
                aggregated.events[event.event_id] = AggregatedOdds(
                    event_id=event.event_id,
                    event_name=event.display_name,
                    sport=event.sport,
                    league=event.league,
                    commence_time=event.commence_time,
                    home_team=event.home_team,
                    away_team=event.away_team,
                )

            agg_event = aggregated.events[event.event_id]

            # Merge odds from all bookmakers
            for bookmaker, markets in event.markets.items():
                for market_key, market in markets.items():
                    if market_key != "h2h":  # Focus on H2H for MVP
                        continue

                    for selection in market.selections:
                        # Normalize selection name
                        normalized_name = self._normalize_selection(
                            selection.name,
                            event.home_team,
                            event.away_team,
                        )

                        agg_event.add_odds(normalized_name, selection)

            source_event_count += 1

        # Update source stats
        aggregated.source_stats[snapshot.source_name] = (
            aggregated.source_stats.get(snapshot.source_name, 0) + source_event_count
        )

    def _normalize_selection(
        self,
        selection_name: str,
        home_team: str,
        away_team: str,
    ) -> str:
        """
        Normalize selection names for consistent matching.

        Maps team names to standard "home" / "away" labels.
        """
        selection_lower = selection_name.lower().strip()
        home_lower = home_team.lower().strip()
        away_lower = away_team.lower().strip()

        # Direct match
        if selection_lower == home_lower or selection_name == home_team:
            return "home"
        if selection_lower == away_lower or selection_name == away_team:
            return "away"

        # Partial match (handles "N. Djokovic" vs "Novak Djokovic")
        if self._fuzzy_match(selection_lower, home_lower):
            return "home"
        if self._fuzzy_match(selection_lower, away_lower):
            return "away"

        # Draw (for soccer)
        if selection_lower in ("draw", "tie", "x"):
            return "draw"

        # Fallback: return original
        return selection_name

    def _fuzzy_match(self, name1: str, name2: str, threshold: float = 0.8) -> bool:
        """Simple fuzzy matching for team names."""
        # Check if one contains the other
        if name1 in name2 or name2 in name1:
            return True

        # Check last name match (for tennis)
        words1 = name1.split()
        words2 = name2.split()

        if words1 and words2:
            # Last word match
            if words1[-1] == words2[-1]:
                return True

        return False

    def _count_unique_bookmakers(self, snapshot: AggregatedSnapshot) -> int:
        """Count unique bookmakers across all events."""
        bookmakers = set()
        for event in snapshot.events.values():
            for selection_odds in event.odds_matrix.values():
                bookmakers.update(selection_odds.keys())
        return len(bookmakers)

    def is_exchange(self, bookmaker: str) -> bool:
        """Check if a bookmaker is an exchange."""
        return bookmaker.lower() in self.EXCHANGES

    def get_commission(self, bookmaker: str) -> float:
        """Get commission rate for a bookmaker/exchange."""
        return self.EXCHANGE_COMMISSIONS.get(bookmaker.lower(), 0.0)

    async def close(self) -> None:
        """Close all sources."""
        for source in self.sources:
            await source.close()

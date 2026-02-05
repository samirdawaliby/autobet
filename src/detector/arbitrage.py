"""
Arbitrage detection engine.

Scans aggregated odds to find surebet opportunities.
Handles commission calculation for exchanges.
"""
import structlog
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from src.sources.aggregator import AggregatedOdds, AggregatedSnapshot, OddsAggregator

logger = structlog.get_logger()


@dataclass
class ArbLeg:
    """A single leg of an arbitrage bet."""
    selection: str  # "home" / "away" / "draw"
    selection_name: str  # Original team name
    bookmaker: str
    odds: float
    effective_odds: float  # After commission
    stake: float
    potential_return: float
    is_exchange: bool
    timestamp: datetime
    liquidity: Optional[float] = None


@dataclass
class ArbOpportunity:
    """A detected arbitrage opportunity."""
    id: str
    event_id: str
    event_name: str
    sport: str
    league: str
    market: str
    commence_time: datetime
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Arbitrage metrics
    edge: float  # Profit percentage (e.g., 1.5 = 1.5% profit)
    implied_probability_sum: float  # < 1 means arbitrage exists

    # Legs
    legs: list[ArbLeg] = field(default_factory=list)

    # Stake calculation (based on 100 unit base)
    total_stake: float = 100.0
    guaranteed_profit: float = 0.0
    roi: float = 0.0  # Return on investment percentage

    # Execution info
    executable_legs: int = 0  # How many legs can be auto-executed
    requires_manual: bool = True  # Needs manual placement on some bookmakers

    # Quality metrics
    bookmaker_count: int = 0
    min_odds_age_seconds: float = 0.0
    max_odds_age_seconds: float = 0.0

    def to_alert_message(self) -> str:
        """Format opportunity for Telegram alert."""
        exec_status = "🤖 AUTO" if not self.requires_manual else "⚠️ SEMI-AUTO"
        if self.executable_legs == 0:
            exec_status = "📢 MANUAL"

        legs_text = "\n".join([
            f"  {'✅' if leg.is_exchange else '📍'} {leg.bookmaker}: "
            f"{leg.selection_name} @ {leg.odds:.2f} → {leg.stake:.2f}€"
            for leg in self.legs
        ])

        return f"""
{exec_status} | Edge: {self.edge:.2f}%

📊 {self.event_name}
🏆 {self.league}
⏰ {self.commence_time.strftime('%H:%M %d/%m')}

{legs_text}

💰 Stake: {self.total_stake:.2f}€
💵 Profit: {self.guaranteed_profit:.2f}€ ({self.roi:.2f}% ROI)
📈 Implied: {self.implied_probability_sum:.4f}
        """.strip()


class ArbitrageDetector:
    """
    Detects arbitrage opportunities from aggregated odds.

    Features:
    - Handles 2-way (tennis) and 3-way (soccer) markets
    - Applies exchange commissions to effective odds
    - Calculates optimal stake distribution
    - Filters by edge, freshness, and liquidity
    """

    # Known exchanges for commission and execution
    EXCHANGES = {
        "betfair", "betfair_ex_eu", "betfair_ex_uk", "betfair_ex_au",
        "smarkets", "matchbook", "betdaq",
    }

    EXCHANGE_COMMISSIONS = {
        "betfair": 0.05,
        "betfair_ex_eu": 0.05,
        "betfair_ex_uk": 0.05,
        "betfair_ex_au": 0.05,
        "smarkets": 0.02,
        "matchbook": 0.02,
        "betdaq": 0.02,
    }

    def __init__(
        self,
        min_edge: float = 0.8,  # Minimum 0.8% edge
        max_odds_age: float = 5.0,  # Maximum 5 seconds old
        min_bookmakers: int = 2,  # Minimum bookmakers for valid arb
        base_stake: float = 100.0,  # Base stake for calculations
    ):
        self.min_edge = min_edge
        self.max_odds_age = max_odds_age
        self.min_bookmakers = min_bookmakers
        self.base_stake = base_stake

    def detect(
        self,
        snapshot: AggregatedSnapshot,
        current_time: Optional[datetime] = None,
    ) -> list[ArbOpportunity]:
        """
        Scan aggregated odds for arbitrage opportunities.

        Args:
            snapshot: Aggregated odds from all sources
            current_time: Current time for freshness check

        Returns:
            List of arbitrage opportunities sorted by edge
        """
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        opportunities = []

        for event_id, event in snapshot.events.items():
            # Check if we have enough bookmakers
            if event.get_bookmaker_count() < self.min_bookmakers:
                continue

            # Detect arbitrage for this event
            opp = self._detect_event_arbitrage(event, current_time)

            if opp and opp.edge >= self.min_edge:
                opportunities.append(opp)

        # Sort by edge (highest first)
        opportunities.sort(key=lambda x: x.edge, reverse=True)

        logger.info(
            "arbitrage_scan_complete",
            events_scanned=len(snapshot.events),
            opportunities_found=len(opportunities),
            best_edge=opportunities[0].edge if opportunities else 0,
        )

        return opportunities

    def _detect_event_arbitrage(
        self,
        event: AggregatedOdds,
        current_time: datetime,
    ) -> Optional[ArbOpportunity]:
        """
        Detect arbitrage for a single event.

        For 2-way markets (tennis): checks home vs away
        For 3-way markets (soccer): checks home vs draw vs away
        """
        selections = list(event.odds_matrix.keys())

        # Need at least 2 selections
        if len(selections) < 2:
            return None

        # Get best odds for each selection (with freshness filter)
        best_odds = {}
        for selection in selections:
            best = self._get_best_odds(
                event.odds_matrix[selection],
                current_time,
            )
            if best:
                best_odds[selection] = best

        # Need odds for all selections
        if len(best_odds) < len(selections):
            return None

        # Calculate implied probability sum
        # Apply commission for exchanges
        total_implied = 0.0
        effective_odds_map = {}

        for selection, (odds, bookmaker, timestamp) in best_odds.items():
            effective = self._apply_commission(odds, bookmaker)
            effective_odds_map[selection] = (effective, odds, bookmaker, timestamp)
            total_implied += 1 / effective

        # Check if arbitrage exists (implied < 1)
        if total_implied >= 1:
            return None

        # Calculate edge
        edge = (1 - total_implied) * 100  # Convert to percentage

        # Build opportunity
        return self._build_opportunity(
            event=event,
            effective_odds_map=effective_odds_map,
            total_implied=total_implied,
            edge=edge,
            current_time=current_time,
        )

    def _get_best_odds(
        self,
        selection_odds: dict[str, "OddsSelection"],
        current_time: datetime,
    ) -> Optional[tuple[float, str, datetime]]:
        """
        Get best odds for a selection, filtering by freshness.

        Returns: (odds, bookmaker, timestamp) or None
        """
        best_odds = 0.0
        best_bookmaker = None
        best_timestamp = None

        for bookmaker, odds_selection in selection_odds.items():
            # Check freshness
            age = (current_time - odds_selection.timestamp).total_seconds()
            if age > self.max_odds_age:
                continue

            if odds_selection.odds > best_odds:
                best_odds = odds_selection.odds
                best_bookmaker = bookmaker
                best_timestamp = odds_selection.timestamp

        if best_bookmaker:
            return (best_odds, best_bookmaker, best_timestamp)
        return None

    def _apply_commission(self, odds: float, bookmaker: str) -> float:
        """
        Apply exchange commission to odds.

        For exchanges, effective odds = 1 + (odds - 1) * (1 - commission)
        For regular bookmakers, no commission on winnings.
        """
        bookmaker_lower = bookmaker.lower()
        if bookmaker_lower in self.EXCHANGES:
            commission = self.EXCHANGE_COMMISSIONS.get(bookmaker_lower, 0.05)
            return 1 + (odds - 1) * (1 - commission)
        return odds

    def _build_opportunity(
        self,
        event: AggregatedOdds,
        effective_odds_map: dict,
        total_implied: float,
        edge: float,
        current_time: datetime,
    ) -> ArbOpportunity:
        """Build a complete ArbOpportunity object."""
        # Calculate stakes for each selection
        # Formula: stake_i = (total_stake / effective_odds_i) / sum(1/effective_odds_j)
        legs = []
        executable_count = 0
        timestamps = []

        for selection, (effective, original, bookmaker, timestamp) in effective_odds_map.items():
            # Stake calculation for equal profit
            stake = (self.base_stake * (1 / effective)) / total_implied
            potential_return = stake * original

            is_exchange = bookmaker.lower() in self.EXCHANGES
            if is_exchange:
                executable_count += 1

            # Get original selection name
            selection_odds = event.odds_matrix.get(selection, {})
            bookmaker_odds = selection_odds.get(bookmaker)
            selection_name = bookmaker_odds.name if bookmaker_odds else selection

            leg = ArbLeg(
                selection=selection,
                selection_name=selection_name,
                bookmaker=bookmaker,
                odds=original,
                effective_odds=effective,
                stake=round(stake, 2),
                potential_return=round(potential_return, 2),
                is_exchange=is_exchange,
                timestamp=timestamp,
            )
            legs.append(leg)
            timestamps.append(timestamp)

        # Calculate guaranteed profit
        # Profit = min(potential_return) - total_stake
        min_return = min(leg.potential_return for leg in legs)
        guaranteed_profit = min_return - self.base_stake
        roi = (guaranteed_profit / self.base_stake) * 100

        # Calculate odds age range
        ages = [(current_time - ts).total_seconds() for ts in timestamps]

        opp = ArbOpportunity(
            id=f"arb_{event.event_id}_{int(current_time.timestamp())}",
            event_id=event.event_id,
            event_name=event.event_name,
            sport=event.sport.value,
            league=event.league,
            market="h2h",
            commence_time=event.commence_time,
            edge=round(edge, 2),
            implied_probability_sum=round(total_implied, 4),
            legs=legs,
            total_stake=self.base_stake,
            guaranteed_profit=round(guaranteed_profit, 2),
            roi=round(roi, 2),
            executable_legs=executable_count,
            requires_manual=executable_count < len(legs),
            bookmaker_count=len(legs),
            min_odds_age_seconds=min(ages),
            max_odds_age_seconds=max(ages),
        )

        return opp


class ValueBetDetector:
    """
    Detects value betting opportunities.

    Uses Pinnacle odds as "true" probability reference
    and finds overpriced odds at other bookmakers.

    (Future implementation)
    """

    SHARP_BOOKMAKERS = {"pinnacle", "pinnaclesports"}

    def __init__(
        self,
        min_value: float = 3.0,  # Minimum 3% value
        kelly_fraction: float = 0.25,  # Quarter Kelly
    ):
        self.min_value = min_value
        self.kelly_fraction = kelly_fraction

    def detect(self, snapshot: AggregatedSnapshot) -> list[dict]:
        """Detect value bets using Pinnacle as reference."""
        # TODO: Implement value betting detection
        # 1. Get Pinnacle odds as "true" probability
        # 2. Find bookmakers with higher odds
        # 3. Calculate expected value
        # 4. Return opportunities with EV > min_value
        return []

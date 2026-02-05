"""
Database repository for AutoBet.
"""
import structlog
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from .models import Base, DailyStats, Opportunity, OpportunityStatus, Order, OrderStatus, RiskState

logger = structlog.get_logger()


class Repository:
    """
    Database repository with async support.
    """

    def __init__(self, database_url: str, echo: bool = False):
        self.engine = create_async_engine(database_url, echo=echo)
        self.async_session = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def init_db(self) -> None:
        """Initialize database tables."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("database_initialized")

    async def close(self) -> None:
        """Close database connection."""
        await self.engine.dispose()

    # =========================================================================
    # Opportunity Operations
    # =========================================================================

    async def save_opportunity(self, opp: "ArbOpportunity") -> Opportunity:
        """Save a detected arbitrage opportunity."""
        async with self.async_session() as session:
            db_opp = Opportunity(
                id=opp.id,
                event_id=opp.event_id,
                event_name=opp.event_name,
                sport=opp.sport,
                league=opp.league,
                market=opp.market,
                commence_time=opp.commence_time,
                edge=opp.edge,
                implied_probability_sum=opp.implied_probability_sum,
                total_stake=opp.total_stake,
                guaranteed_profit=opp.guaranteed_profit,
                roi=opp.roi,
                legs=[
                    {
                        "selection": leg.selection,
                        "selection_name": leg.selection_name,
                        "bookmaker": leg.bookmaker,
                        "odds": leg.odds,
                        "effective_odds": leg.effective_odds,
                        "stake": leg.stake,
                        "potential_return": leg.potential_return,
                        "is_exchange": leg.is_exchange,
                    }
                    for leg in opp.legs
                ],
                executable_legs=opp.executable_legs,
                requires_manual=opp.requires_manual,
                status=OpportunityStatus.DETECTED,
                detected_at=opp.detected_at,
                bookmaker_count=opp.bookmaker_count,
                min_odds_age_seconds=opp.min_odds_age_seconds,
                max_odds_age_seconds=opp.max_odds_age_seconds,
            )
            session.add(db_opp)
            await session.commit()
            return db_opp

    async def update_opportunity_status(
        self,
        opp_id: str,
        status: OpportunityStatus,
        actual_profit: Optional[float] = None,
    ) -> None:
        """Update opportunity status."""
        async with self.async_session() as session:
            result = await session.execute(
                select(Opportunity).where(Opportunity.id == opp_id)
            )
            opp = result.scalar_one_or_none()
            if opp:
                opp.status = status
                if actual_profit is not None:
                    opp.actual_profit = actual_profit
                if status == OpportunityStatus.EXECUTED:
                    opp.executed_at = datetime.now(timezone.utc)
                await session.commit()

    async def get_recent_opportunities(
        self,
        limit: int = 50,
        status: Optional[OpportunityStatus] = None,
    ) -> list[Opportunity]:
        """Get recent opportunities."""
        async with self.async_session() as session:
            query = select(Opportunity).order_by(Opportunity.detected_at.desc())
            if status:
                query = query.where(Opportunity.status == status)
            query = query.limit(limit)
            result = await session.execute(query)
            return list(result.scalars().all())

    # =========================================================================
    # Order Operations
    # =========================================================================

    async def save_order(self, order: Order) -> Order:
        """Save a bet order."""
        async with self.async_session() as session:
            session.add(order)
            await session.commit()
            return order

    async def update_order_status(
        self,
        order_id: str,
        status: OrderStatus,
        filled_odds: Optional[float] = None,
        filled_stake: Optional[float] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Update order status."""
        async with self.async_session() as session:
            result = await session.execute(
                select(Order).where(Order.id == order_id)
            )
            order = result.scalar_one_or_none()
            if order:
                order.status = status
                if filled_odds:
                    order.filled_odds = filled_odds
                if filled_stake:
                    order.filled_stake = filled_stake
                if error_message:
                    order.error_message = error_message
                if status == OrderStatus.FILLED:
                    order.filled_at = datetime.now(timezone.utc)
                await session.commit()

    # =========================================================================
    # Risk State Operations
    # =========================================================================

    async def get_or_create_risk_state(
        self,
        initial_bankroll: float = 1000.0,
    ) -> RiskState:
        """Get or create the risk state."""
        async with self.async_session() as session:
            result = await session.execute(select(RiskState))
            state = result.scalar_one_or_none()

            if not state:
                state = RiskState(
                    initial_bankroll=initial_bankroll,
                    current_bankroll=initial_bankroll,
                )
                session.add(state)
                await session.commit()

            # Check if daily reset needed
            now = datetime.now(timezone.utc)
            if state.daily_reset_at.date() < now.date():
                state.daily_stake = 0
                state.daily_pnl = 0
                state.daily_trades = 0
                state.daily_wins = 0
                state.daily_reset_at = now
                await session.commit()

            return state

    async def update_risk_state(
        self,
        stake: float,
        pnl: float,
        is_win: bool,
    ) -> RiskState:
        """Update risk state after a trade."""
        async with self.async_session() as session:
            result = await session.execute(select(RiskState))
            state = result.scalar_one_or_none()

            if state:
                # Update daily metrics
                state.daily_stake += stake
                state.daily_pnl += pnl
                state.daily_trades += 1
                if is_win:
                    state.daily_wins += 1

                # Update cumulative metrics
                state.total_stake += stake
                state.total_pnl += pnl
                state.total_trades += 1
                if is_win:
                    state.total_wins += 1

                # Update bankroll
                state.current_bankroll += pnl
                state.last_trade_at = datetime.now(timezone.utc)

                await session.commit()

            return state

    async def set_kill_switch(self, active: bool, reason: Optional[str] = None) -> None:
        """Set or clear kill switch."""
        async with self.async_session() as session:
            result = await session.execute(select(RiskState))
            state = result.scalar_one_or_none()
            if state:
                state.kill_switch_active = active
                state.kill_switch_reason = reason
                await session.commit()

    # =========================================================================
    # Statistics Operations
    # =========================================================================

    async def get_or_create_daily_stats(self, date: str) -> DailyStats:
        """Get or create daily stats for a date."""
        async with self.async_session() as session:
            result = await session.execute(
                select(DailyStats).where(DailyStats.date == date)
            )
            stats = result.scalar_one_or_none()

            if not stats:
                stats = DailyStats(date=date)
                session.add(stats)
                await session.commit()

            return stats

    async def increment_daily_scan(self, events_scanned: int, opportunities: int) -> None:
        """Increment daily scan statistics."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        async with self.async_session() as session:
            result = await session.execute(
                select(DailyStats).where(DailyStats.date == today)
            )
            stats = result.scalar_one_or_none()

            if not stats:
                stats = DailyStats(date=today)
                session.add(stats)

            stats.scans_count += 1
            stats.events_scanned += events_scanned
            stats.opportunities_detected += opportunities

            await session.commit()

    async def get_dashboard_stats(self) -> dict:
        """Get statistics for dashboard."""
        async with self.async_session() as session:
            # Get risk state
            risk_result = await session.execute(select(RiskState))
            risk_state = risk_result.scalar_one_or_none()

            # Get today's stats
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            stats_result = await session.execute(
                select(DailyStats).where(DailyStats.date == today)
            )
            daily_stats = stats_result.scalar_one_or_none()

            # Get recent opportunities count
            recent_result = await session.execute(
                select(func.count(Opportunity.id)).where(
                    Opportunity.detected_at > datetime.now(timezone.utc) - timedelta(hours=24)
                )
            )
            recent_count = recent_result.scalar() or 0

            return {
                "risk_state": {
                    "current_bankroll": risk_state.current_bankroll if risk_state else 0,
                    "initial_bankroll": risk_state.initial_bankroll if risk_state else 0,
                    "daily_pnl": risk_state.daily_pnl if risk_state else 0,
                    "total_pnl": risk_state.total_pnl if risk_state else 0,
                    "daily_trades": risk_state.daily_trades if risk_state else 0,
                    "total_trades": risk_state.total_trades if risk_state else 0,
                    "kill_switch_active": risk_state.kill_switch_active if risk_state else False,
                },
                "daily_stats": {
                    "scans_count": daily_stats.scans_count if daily_stats else 0,
                    "events_scanned": daily_stats.events_scanned if daily_stats else 0,
                    "opportunities_detected": daily_stats.opportunities_detected if daily_stats else 0,
                    "opportunities_executed": daily_stats.opportunities_executed if daily_stats else 0,
                },
                "recent_opportunities": recent_count,
            }

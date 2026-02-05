"""
Main Scanner - Orchestrates the arbitrage detection pipeline.
"""
import asyncio
import structlog
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import Settings
from src.sources.aggregator import OddsAggregator
from src.sources.base import Market, Sport
from src.detector.arbitrage import ArbitrageDetector, ArbOpportunity
from src.storage.repository import Repository
from src.monitoring.telegram_bot import TelegramAlerter

logger = structlog.get_logger()


class Scanner:
    """
    Main scanner that orchestrates:
    1. Fetching odds from multiple sources
    2. Detecting arbitrage opportunities
    3. Alerting via Telegram
    4. Storing results
    5. (Optional) Executing bets
    """

    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        aggregator: Optional[OddsAggregator] = None,
        alerter: Optional[TelegramAlerter] = None,
    ):
        self.settings = settings
        self.repository = repository

        # Initialize components
        self.aggregator = aggregator or OddsAggregator.from_config(settings)
        self.detector = ArbitrageDetector(
            min_edge=settings.risk.min_edge_percent,
            max_odds_age=settings.risk.max_odds_age_seconds,
            base_stake=100.0,  # Calculate stakes for 100€ base
        )
        self.alerter = alerter

        # Scheduler
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._running = False

        # State
        self.last_scan_time: Optional[datetime] = None
        self.scan_count = 0

    @property
    def is_running(self) -> bool:
        return self._running

    async def initialize(self) -> None:
        """Initialize all components."""
        # Initialize database
        await self.repository.init_db()

        # Initialize risk state
        await self.repository.get_or_create_risk_state(
            initial_bankroll=self.settings.risk.initial_bankroll
        )

        # Initialize Telegram if configured
        if self.alerter:
            await self.alerter.initialize()
            self._setup_alerter_callbacks()
            await self.alerter.send_startup_message()

        logger.info("scanner_initialized", mode=self.settings.mode)

    def _setup_alerter_callbacks(self) -> None:
        """Setup Telegram command callbacks."""
        if not self.alerter:
            return

        async def get_status() -> dict:
            risk_state = await self.repository.get_or_create_risk_state()
            stats = await self.repository.get_dashboard_stats()
            return {
                "mode": self.settings.mode,
                "scanner_running": self._running,
                "last_scan": self.last_scan_time.isoformat() if self.last_scan_time else None,
                "events_tracked": stats["daily_stats"]["events_scanned"],
                "opportunities_today": stats["daily_stats"]["opportunities_detected"],
                "bankroll": risk_state.current_bankroll,
                "daily_pnl": risk_state.daily_pnl,
                "kill_switch": risk_state.kill_switch_active,
            }

        async def get_stats() -> dict:
            stats = await self.repository.get_dashboard_stats()
            risk = stats["risk_state"]
            daily = stats["daily_stats"]
            return {
                "scans": daily["scans_count"],
                "events_scanned": daily["events_scanned"],
                "opportunities_detected": daily["opportunities_detected"],
                "executed": daily["opportunities_executed"],
                "partial": 0,  # TODO: track this
                "failed": 0,  # TODO: track this
                "total_stake": risk["daily_pnl"],  # Simplified
                "total_pnl": risk["daily_pnl"],
                "roi": 0,  # TODO: calculate
                "best_edge": 0,  # TODO: track
                "avg_edge": 0,  # TODO: track
            }

        async def get_opportunities() -> list:
            opps = await self.repository.get_recent_opportunities(limit=5)
            return [
                {
                    "event_name": opp.event_name,
                    "edge": opp.edge,
                    "guaranteed_profit": opp.guaranteed_profit,
                    "status": opp.status.value,
                }
                for opp in opps
            ]

        async def get_risk() -> dict:
            risk_state = await self.repository.get_or_create_risk_state()
            return {
                "initial_bankroll": risk_state.initial_bankroll,
                "current_bankroll": risk_state.current_bankroll,
                "total_pnl": risk_state.total_pnl,
                "total_roi": (risk_state.total_pnl / risk_state.initial_bankroll * 100)
                if risk_state.initial_bankroll > 0 else 0,
                "daily_stake": risk_state.daily_stake,
                "daily_limit": risk_state.initial_bankroll * self.settings.risk.max_daily_stake_percent,
                "daily_drawdown": abs(min(0, risk_state.daily_pnl)) / risk_state.initial_bankroll * 100
                if risk_state.initial_bankroll > 0 else 0,
                "max_drawdown": self.settings.risk.max_daily_drawdown_percent * 100,
                "total_trades": risk_state.total_trades,
                "win_rate": (risk_state.total_wins / risk_state.total_trades * 100)
                if risk_state.total_trades > 0 else 0,
                "kill_switch": risk_state.kill_switch_active,
                "kill_reason": risk_state.kill_switch_reason,
            }

        async def set_killswitch(active: bool) -> None:
            reason = "Manual activation via Telegram" if active else None
            await self.repository.set_kill_switch(active, reason)
            if active:
                await self.alerter.send_risk_alert(
                    "killswitch",
                    {"reason": reason, "daily_pnl": 0, "drawdown": 0}
                )

        async def set_mode(mode: str) -> None:
            self.settings.mode = mode
            logger.info("mode_changed", mode=mode)

        self.alerter.set_callbacks(
            status=get_status,
            stats=get_stats,
            opportunities=get_opportunities,
            risk=get_risk,
            killswitch=set_killswitch,
            mode=set_mode,
        )

    async def start(self) -> None:
        """Start the scanner with scheduled execution."""
        if self._running:
            logger.warning("scanner_already_running")
            return

        self._running = True

        # Create scheduler
        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self.scan_once,
            trigger=IntervalTrigger(seconds=self.settings.scan_interval_seconds),
            id="main_scan",
            name="Main arbitrage scan",
            replace_existing=True,
        )
        self._scheduler.start()

        # Run initial scan
        await self.scan_once()

        logger.info(
            "scanner_started",
            interval_seconds=self.settings.scan_interval_seconds,
            mode=self.settings.mode,
        )

    async def stop(self) -> None:
        """Stop the scanner."""
        self._running = False

        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

        logger.info("scanner_stopped")

    async def scan_once(self) -> list[ArbOpportunity]:
        """
        Run a single scan cycle.

        Returns:
            List of detected opportunities
        """
        scan_start = datetime.now(timezone.utc)
        self.scan_count += 1

        logger.info("scan_started", scan_number=self.scan_count)

        try:
            # Check kill switch
            risk_state = await self.repository.get_or_create_risk_state()
            if risk_state.kill_switch_active:
                logger.warning("scan_skipped_kill_switch_active")
                return []

            # 1. Fetch odds from all sources
            sports = [Sport(s) for s in self.settings.sports]
            markets = [Market.H2H]

            snapshot = await self.aggregator.fetch_all(sports, markets)

            # 2. Detect arbitrage opportunities
            opportunities = self.detector.detect(snapshot, scan_start)

            # 3. Store opportunities
            for opp in opportunities:
                await self.repository.save_opportunity(opp)

            # 4. Update daily stats
            await self.repository.increment_daily_scan(
                events_scanned=len(snapshot.events),
                opportunities=len(opportunities),
            )

            # 5. Send alerts for significant opportunities
            if self.alerter:
                for opp in opportunities:
                    await self.alerter.send_opportunity_alert(opp)

            # 6. Execute if in auto mode
            if self.settings.mode == "auto" and opportunities:
                # TODO: Implement execution
                pass

            self.last_scan_time = scan_start

            logger.info(
                "scan_completed",
                scan_number=self.scan_count,
                events_scanned=len(snapshot.events),
                opportunities_found=len(opportunities),
                duration_ms=(datetime.now(timezone.utc) - scan_start).total_seconds() * 1000,
            )

            return opportunities

        except Exception as e:
            logger.error(
                "scan_error",
                scan_number=self.scan_count,
                error=str(e),
            )
            if self.alerter:
                await self.alerter.send_alert(f"❌ Scan error: {str(e)}")
            return []

    async def shutdown(self) -> None:
        """Cleanup and shutdown."""
        await self.stop()

        if self.alerter:
            await self.alerter.stop()

        await self.aggregator.close()
        await self.repository.close()

        logger.info("scanner_shutdown_complete")

"""
Telegram Bot for AutoBet alerts and monitoring.

Features:
- Real-time arbitrage alerts
- Execution notifications
- Status commands
- Manual execution approval (semi-auto mode)
"""
import asyncio
import structlog
from datetime import datetime, timezone
from typing import Optional, Callable, Awaitable

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logger = structlog.get_logger()


class TelegramAlerter:
    """
    Telegram bot for alerts and commands.

    Commands:
    /status - Current system status
    /stats - Today's statistics
    /opportunities - Recent opportunities
    /risk - Risk management state
    /killswitch - Toggle kill switch
    /mode - Change execution mode
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        min_edge_alert: float = 0.5,
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.min_edge_alert = min_edge_alert

        self._bot: Optional[Bot] = None
        self._app: Optional[Application] = None
        self._running = False

        # Callbacks for commands
        self._status_callback: Optional[Callable[[], Awaitable[dict]]] = None
        self._stats_callback: Optional[Callable[[], Awaitable[dict]]] = None
        self._opportunities_callback: Optional[Callable[[], Awaitable[list]]] = None
        self._risk_callback: Optional[Callable[[], Awaitable[dict]]] = None
        self._killswitch_callback: Optional[Callable[[bool], Awaitable[None]]] = None
        self._mode_callback: Optional[Callable[[str], Awaitable[None]]] = None

    async def initialize(self) -> None:
        """Initialize the bot."""
        self._bot = Bot(token=self.bot_token)
        self._app = Application.builder().token(self.bot_token).build()

        # Register command handlers
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("stats", self._cmd_stats))
        self._app.add_handler(CommandHandler("opportunities", self._cmd_opportunities))
        self._app.add_handler(CommandHandler("risk", self._cmd_risk))
        self._app.add_handler(CommandHandler("killswitch", self._cmd_killswitch))
        self._app.add_handler(CommandHandler("mode", self._cmd_mode))
        self._app.add_handler(CommandHandler("help", self._cmd_help))

        # Message handler for quick responses
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

        logger.info("telegram_bot_initialized")

    async def start_polling(self) -> None:
        """Start the bot in polling mode."""
        if self._app:
            self._running = True
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling()
            logger.info("telegram_bot_polling_started")

    async def stop(self) -> None:
        """Stop the bot."""
        self._running = False
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        logger.info("telegram_bot_stopped")

    def set_callbacks(
        self,
        status: Optional[Callable] = None,
        stats: Optional[Callable] = None,
        opportunities: Optional[Callable] = None,
        risk: Optional[Callable] = None,
        killswitch: Optional[Callable] = None,
        mode: Optional[Callable] = None,
    ) -> None:
        """Set callback functions for commands."""
        if status:
            self._status_callback = status
        if stats:
            self._stats_callback = stats
        if opportunities:
            self._opportunities_callback = opportunities
        if risk:
            self._risk_callback = risk
        if killswitch:
            self._killswitch_callback = killswitch
        if mode:
            self._mode_callback = mode

    # =========================================================================
    # Alert Methods
    # =========================================================================

    async def send_alert(self, message: str) -> None:
        """Send a simple text alert."""
        if self._bot:
            try:
                await self._bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error("telegram_send_error", error=str(e))

    async def send_opportunity_alert(self, opp: "ArbOpportunity") -> None:
        """Send an arbitrage opportunity alert."""
        if opp.edge < self.min_edge_alert:
            return

        message = self._format_opportunity(opp)
        await self.send_alert(message)

    async def send_execution_alert(
        self,
        opp: "ArbOpportunity",
        success: bool,
        actual_profit: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        """Send execution result alert."""
        if success:
            message = f"""
✅ <b>EXECUTION SUCCESS</b>

📊 {opp.event_name}
💰 Expected profit: {opp.guaranteed_profit:.2f}€
💵 Actual profit: {actual_profit:.2f}€ if actual_profit else 'Pending'

Edge: {opp.edge}% | ROI: {opp.roi}%
            """.strip()
        else:
            message = f"""
❌ <b>EXECUTION FAILED</b>

📊 {opp.event_name}
⚠️ Error: {error or 'Unknown error'}

Edge was: {opp.edge}%
            """.strip()

        await self.send_alert(message)

    async def send_risk_alert(self, alert_type: str, details: dict) -> None:
        """Send risk management alert."""
        if alert_type == "killswitch":
            message = f"""
🚨 <b>KILL SWITCH ACTIVATED</b>

Reason: {details.get('reason', 'Unknown')}
Daily PnL: {details.get('daily_pnl', 0):.2f}€
Drawdown: {details.get('drawdown', 0):.2f}%

⚠️ Automatic trading paused.
Use /killswitch off to resume.
            """.strip()
        elif alert_type == "daily_limit":
            message = f"""
⚠️ <b>DAILY LIMIT REACHED</b>

Daily stake: {details.get('daily_stake', 0):.2f}€
Limit: {details.get('limit', 0):.2f}€

Trading paused until tomorrow.
            """.strip()
        else:
            message = f"⚠️ Risk Alert: {alert_type}\n{details}"

        await self.send_alert(message)

    async def send_startup_message(self) -> None:
        """Send bot startup notification."""
        message = """
🤖 <b>AutoBet Scanner Started</b>

Commands:
/status - System status
/stats - Today's statistics
/opportunities - Recent opportunities
/risk - Risk management state
/killswitch - Toggle kill switch
/help - All commands

Bot is now monitoring for arbitrage opportunities.
        """.strip()
        await self.send_alert(message)

    # =========================================================================
    # Command Handlers
    # =========================================================================

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        await update.message.reply_text(
            "👋 Welcome to AutoBet Scanner!\n\n"
            "Use /help to see available commands."
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        help_text = """
<b>AutoBet Commands</b>

📊 <b>Monitoring</b>
/status - Current system status
/stats - Today's statistics
/opportunities - Recent opportunities

💰 <b>Risk Management</b>
/risk - Risk state and limits
/killswitch [on|off] - Toggle kill switch

⚙️ <b>Settings</b>
/mode [dry|semi|auto] - Change execution mode

<b>Quick Responses</b>
Reply "GO" to execute pending semi-auto opportunities
Reply "SKIP" to skip an opportunity
        """.strip()
        await update.message.reply_html(help_text)

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        if self._status_callback:
            data = await self._status_callback()
            message = f"""
<b>System Status</b>

🔄 Mode: {data.get('mode', 'unknown')}
📡 Scanner: {'Running' if data.get('scanner_running') else 'Stopped'}
⏰ Last scan: {data.get('last_scan', 'Never')}

📊 Events tracked: {data.get('events_tracked', 0)}
🎯 Opportunities today: {data.get('opportunities_today', 0)}

💰 Bankroll: {data.get('bankroll', 0):.2f}€
📈 Daily PnL: {data.get('daily_pnl', 0):+.2f}€

🔒 Kill switch: {'🔴 ACTIVE' if data.get('kill_switch') else '🟢 OFF'}
            """.strip()
        else:
            message = "Status callback not configured."

        await update.message.reply_html(message)

    async def _cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /stats command."""
        if self._stats_callback:
            data = await self._stats_callback()
            message = f"""
<b>Today's Statistics</b>

🔍 Scans: {data.get('scans', 0)}
📊 Events scanned: {data.get('events_scanned', 0)}
🎯 Opportunities detected: {data.get('opportunities_detected', 0)}

✅ Executed: {data.get('executed', 0)}
⚠️ Partial: {data.get('partial', 0)}
❌ Failed: {data.get('failed', 0)}

💰 Total stake: {data.get('total_stake', 0):.2f}€
💵 Total PnL: {data.get('total_pnl', 0):+.2f}€
📈 ROI: {data.get('roi', 0):.2f}%

🏆 Best edge: {data.get('best_edge', 0):.2f}%
📊 Avg edge: {data.get('avg_edge', 0):.2f}%
            """.strip()
        else:
            message = "Stats callback not configured."

        await update.message.reply_html(message)

    async def _cmd_opportunities(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /opportunities command."""
        if self._opportunities_callback:
            opps = await self._opportunities_callback()

            if not opps:
                message = "No recent opportunities."
            else:
                lines = ["<b>Recent Opportunities</b>\n"]
                for opp in opps[:5]:
                    status_emoji = {
                        "detected": "🔵",
                        "executed": "✅",
                        "failed": "❌",
                        "partial": "⚠️",
                        "expired": "⏰",
                    }.get(opp.get('status', ''), "❓")

                    lines.append(
                        f"{status_emoji} {opp.get('event_name', 'Unknown')}\n"
                        f"   Edge: {opp.get('edge', 0):.2f}% | "
                        f"Profit: {opp.get('guaranteed_profit', 0):.2f}€"
                    )
                message = "\n".join(lines)
        else:
            message = "Opportunities callback not configured."

        await update.message.reply_html(message)

    async def _cmd_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /risk command."""
        if self._risk_callback:
            data = await self._risk_callback()
            message = f"""
<b>Risk Management State</b>

💰 <b>Bankroll</b>
Initial: {data.get('initial_bankroll', 0):.2f}€
Current: {data.get('current_bankroll', 0):.2f}€
Change: {data.get('total_pnl', 0):+.2f}€ ({data.get('total_roi', 0):+.2f}%)

📊 <b>Daily Limits</b>
Daily stake: {data.get('daily_stake', 0):.2f}€ / {data.get('daily_limit', 0):.2f}€
Daily drawdown: {data.get('daily_drawdown', 0):.2f}% / {data.get('max_drawdown', 0):.2f}%

📈 <b>Statistics</b>
Total trades: {data.get('total_trades', 0)}
Win rate: {data.get('win_rate', 0):.1f}%

🔒 Kill switch: {'🔴 ACTIVE - ' + data.get('kill_reason', '') if data.get('kill_switch') else '🟢 OFF'}
            """.strip()
        else:
            message = "Risk callback not configured."

        await update.message.reply_html(message)

    async def _cmd_killswitch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /killswitch command."""
        args = context.args
        if not args:
            await update.message.reply_text(
                "Usage: /killswitch [on|off]\n"
                "Example: /killswitch on"
            )
            return

        action = args[0].lower()
        if action not in ("on", "off"):
            await update.message.reply_text("Invalid option. Use 'on' or 'off'.")
            return

        if self._killswitch_callback:
            await self._killswitch_callback(action == "on")
            status = "activated" if action == "on" else "deactivated"
            await update.message.reply_text(f"✅ Kill switch {status}.")
        else:
            await update.message.reply_text("Kill switch callback not configured.")

    async def _cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /mode command."""
        args = context.args
        if not args:
            await update.message.reply_text(
                "Usage: /mode [dry|semi|auto]\n\n"
                "• dry - Detect only, no execution\n"
                "• semi - Alert and wait for confirmation\n"
                "• auto - Automatic execution"
            )
            return

        mode = args[0].lower()
        if mode not in ("dry", "semi", "auto"):
            await update.message.reply_text("Invalid mode. Use 'dry', 'semi', or 'auto'.")
            return

        if self._mode_callback:
            await self._mode_callback(mode)
            await update.message.reply_text(f"✅ Mode changed to: {mode}")
        else:
            await update.message.reply_text("Mode callback not configured.")

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle regular messages (for quick responses)."""
        text = update.message.text.upper().strip()

        if text == "GO":
            await update.message.reply_text("🚀 Executing pending opportunity...")
            # TODO: Trigger execution of pending semi-auto opportunity
        elif text == "SKIP":
            await update.message.reply_text("⏭️ Skipping current opportunity.")
            # TODO: Skip pending opportunity
        else:
            await update.message.reply_text(
                "Unknown command. Use /help for available commands."
            )

    # =========================================================================
    # Formatting Helpers
    # =========================================================================

    def _format_opportunity(self, opp: "ArbOpportunity") -> str:
        """Format opportunity for Telegram message."""
        exec_status = "🤖 AUTO" if not opp.requires_manual else "⚠️ SEMI-AUTO"
        if opp.executable_legs == 0:
            exec_status = "📢 MANUAL"

        legs_text = "\n".join([
            f"  {'✅' if leg.is_exchange else '📍'} <b>{leg.bookmaker}</b>: "
            f"{leg.selection_name} @ {leg.odds:.2f} → {leg.stake:.2f}€"
            for leg in opp.legs
        ])

        return f"""
<b>{exec_status}</b> | Edge: <b>{opp.edge:.2f}%</b>

📊 {opp.event_name}
🏆 {opp.league}
⏰ {opp.commence_time.strftime('%H:%M %d/%m')}

{legs_text}

💰 Stake: {opp.total_stake:.2f}€
💵 Profit: <b>{opp.guaranteed_profit:.2f}€</b> ({opp.roi:.2f}% ROI)
        """.strip()

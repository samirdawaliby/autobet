"""
CLI interface for AutoBet Scanner.
"""
import asyncio
import structlog
import typer
from rich.console import Console
from rich.table import Table
from typing import Optional

from config.settings import settings

# Configure logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

app = typer.Typer(
    name="autobet",
    help="🤖 AutoBet Scanner - Sports Arbitrage Detection & Execution",
    add_completion=False,
)
console = Console()


@app.command()
def run(
    mode: str = typer.Option(
        "dry",
        "--mode", "-m",
        help="Execution mode: dry (detect only), semi-auto (confirm), auto (execute)"
    ),
    interval: int = typer.Option(
        60,
        "--interval", "-i",
        help="Scan interval in seconds"
    ),
    dashboard: bool = typer.Option(
        False,
        "--dashboard", "-d",
        help="Start web dashboard"
    ),
    telegram: bool = typer.Option(
        True,
        "--telegram/--no-telegram",
        help="Enable Telegram alerts"
    ),
):
    """
    🚀 Start the AutoBet scanner.

    Examples:
        autobet run --mode dry
        autobet run --mode auto --interval 30 --dashboard
    """
    console.print(f"[bold green]🤖 Starting AutoBet Scanner[/bold green]")
    console.print(f"   Mode: [cyan]{mode}[/cyan]")
    console.print(f"   Interval: [cyan]{interval}s[/cyan]")
    console.print(f"   Dashboard: [cyan]{dashboard}[/cyan]")
    console.print(f"   Telegram: [cyan]{telegram}[/cyan]")
    console.print()

    # Update settings
    settings.mode = mode
    settings.scan_interval_seconds = interval

    asyncio.run(_run_scanner(dashboard, telegram))


async def _run_scanner(with_dashboard: bool, with_telegram: bool):
    """Run the scanner asynchronously."""
    from src.scanner import Scanner
    from src.storage.repository import Repository
    from src.monitoring.telegram_bot import TelegramAlerter

    # Initialize repository
    repository = Repository(
        database_url=settings.database.url,
        echo=settings.debug,
    )

    # Initialize alerter if configured
    alerter = None
    if with_telegram and settings.telegram.enabled and settings.telegram.bot_token:
        alerter = TelegramAlerter(
            bot_token=settings.telegram.bot_token,
            chat_id=settings.telegram.chat_id,
            min_edge_alert=settings.telegram.alert_min_edge,
        )

    # Initialize scanner
    scanner = Scanner(
        settings=settings,
        repository=repository,
        alerter=alerter,
    )

    await scanner.initialize()

    # Start dashboard if requested
    if with_dashboard:
        import uvicorn
        from src.monitoring.dashboard import app as dashboard_app, init_dashboard

        init_dashboard(repository, scanner, settings)

        config = uvicorn.Config(
            dashboard_app,
            host="0.0.0.0",
            port=8080,
            log_level="info",
        )
        server = uvicorn.Server(config)

        # Run dashboard and scanner concurrently
        await asyncio.gather(
            server.serve(),
            scanner.start(),
            alerter.start_polling() if alerter else asyncio.sleep(0),
        )
    else:
        # Run scanner and alerter
        await scanner.start()
        if alerter:
            await alerter.start_polling()

        # Keep running
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Shutting down...[/yellow]")
        finally:
            await scanner.shutdown()


@app.command()
def scan(
    once: bool = typer.Option(
        True,
        "--once",
        help="Run a single scan"
    ),
):
    """
    🔍 Run a single scan (for testing).
    """
    console.print("[bold]Running single scan...[/bold]")
    asyncio.run(_scan_once())


async def _scan_once():
    """Run a single scan."""
    from src.scanner import Scanner
    from src.storage.repository import Repository

    repository = Repository(database_url=settings.database.url)
    scanner = Scanner(settings=settings, repository=repository)

    await scanner.initialize()
    opportunities = await scanner.scan_once()

    if opportunities:
        table = Table(title="Detected Opportunities")
        table.add_column("Event", style="cyan")
        table.add_column("Edge", style="green")
        table.add_column("Profit", style="yellow")
        table.add_column("Bookmakers", style="magenta")

        for opp in opportunities:
            bookies = " vs ".join([leg.bookmaker for leg in opp.legs])
            table.add_row(
                opp.event_name,
                f"{opp.edge:.2f}%",
                f"{opp.guaranteed_profit:.2f}€",
                bookies,
            )

        console.print(table)
    else:
        console.print("[yellow]No opportunities found[/yellow]")

    await scanner.shutdown()


@app.command()
def status():
    """
    📊 Show current status.
    """
    asyncio.run(_show_status())


async def _show_status():
    """Show status."""
    from src.storage.repository import Repository

    repository = Repository(database_url=settings.database.url)
    await repository.init_db()

    stats = await repository.get_dashboard_stats()
    risk = stats["risk_state"]
    daily = stats["daily_stats"]

    console.print()
    console.print("[bold]💰 Bankroll[/bold]")
    console.print(f"   Current: [green]{risk['current_bankroll']:.2f}€[/green]")
    console.print(f"   Initial: {risk['initial_bankroll']:.2f}€")

    pnl_color = "green" if risk['daily_pnl'] >= 0 else "red"
    console.print(f"   Daily PnL: [{pnl_color}]{risk['daily_pnl']:+.2f}€[/{pnl_color}]")
    console.print(f"   Total PnL: [{pnl_color}]{risk['total_pnl']:+.2f}€[/{pnl_color}]")

    console.print()
    console.print("[bold]📊 Today[/bold]")
    console.print(f"   Scans: {daily['scans_count']}")
    console.print(f"   Events scanned: {daily['events_scanned']}")
    console.print(f"   Opportunities: {daily['opportunities_detected']}")
    console.print(f"   Executed: {daily['opportunities_executed']}")

    console.print()
    kill_status = "[red]🔴 ACTIVE[/red]" if risk['kill_switch_active'] else "[green]🟢 OFF[/green]"
    console.print(f"[bold]🔒 Kill Switch:[/bold] {kill_status}")

    await repository.close()


@app.command()
def config():
    """
    ⚙️ Show current configuration.
    """
    console.print()
    console.print("[bold]Configuration[/bold]")
    console.print()

    console.print("[cyan]App Settings[/cyan]")
    console.print(f"   Mode: {settings.mode}")
    console.print(f"   Scan interval: {settings.scan_interval_seconds}s")
    console.print(f"   Sports: {settings.sports}")

    console.print()
    console.print("[cyan]Risk Settings[/cyan]")
    console.print(f"   Initial bankroll: {settings.risk.initial_bankroll}€")
    console.print(f"   Max stake/bet: {settings.risk.max_stake_percent * 100}%")
    console.print(f"   Max stake/day: {settings.risk.max_daily_stake_percent * 100}%")
    console.print(f"   Min edge: {settings.risk.min_edge_percent}%")
    console.print(f"   Max odds age: {settings.risk.max_odds_age_seconds}s")

    console.print()
    console.print("[cyan]API Status[/cyan]")
    odds_api_status = "✅ Configured" if settings.odds_api.api_key else "❌ Not configured"
    telegram_status = "✅ Configured" if settings.telegram.bot_token else "❌ Not configured"
    console.print(f"   The Odds API: {odds_api_status}")
    console.print(f"   Telegram: {telegram_status}")


@app.command()
def version():
    """
    📦 Show version.
    """
    console.print("[bold]AutoBet Scanner[/bold] v0.1.0")


if __name__ == "__main__":
    app()

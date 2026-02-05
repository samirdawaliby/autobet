"""
Web Dashboard for AutoBet monitoring.

Features:
- Real-time statistics
- Recent opportunities table
- PnL chart
- Risk status
- Configuration panel
"""
import structlog
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logger = structlog.get_logger()

# Create FastAPI app
app = FastAPI(
    title="AutoBet Dashboard",
    description="Sports Arbitrage Scanner & Executor",
    version="0.1.0",
)


# =========================================================================
# Pydantic Models
# =========================================================================

class StatsResponse(BaseModel):
    """Dashboard statistics."""
    current_bankroll: float
    initial_bankroll: float
    daily_pnl: float
    total_pnl: float
    daily_trades: int
    total_trades: int
    kill_switch_active: bool
    scans_today: int
    opportunities_today: int
    executed_today: int
    mode: str
    scanner_running: bool
    last_scan: Optional[str]


class OpportunityResponse(BaseModel):
    """Opportunity data for API."""
    id: str
    event_name: str
    sport: str
    league: str
    edge: float
    guaranteed_profit: float
    roi: float
    status: str
    detected_at: str
    legs: list[dict]
    requires_manual: bool


class SettingsUpdate(BaseModel):
    """Settings update request."""
    mode: Optional[str] = None
    min_edge: Optional[float] = None
    max_stake_percent: Optional[float] = None
    kill_switch: Optional[bool] = None


# =========================================================================
# Global State (injected at runtime)
# =========================================================================

_dashboard_state = {
    "repository": None,
    "scanner": None,
    "settings": None,
}


def init_dashboard(repository, scanner, settings) -> None:
    """Initialize dashboard with dependencies."""
    _dashboard_state["repository"] = repository
    _dashboard_state["scanner"] = scanner
    _dashboard_state["settings"] = settings


# =========================================================================
# API Endpoints
# =========================================================================

@app.get("/api/stats", response_model=StatsResponse)
async def get_stats():
    """Get current statistics."""
    repo = _dashboard_state.get("repository")
    scanner = _dashboard_state.get("scanner")
    settings = _dashboard_state.get("settings")

    if not repo:
        raise HTTPException(status_code=500, detail="Repository not initialized")

    stats = await repo.get_dashboard_stats()

    return StatsResponse(
        current_bankroll=stats["risk_state"]["current_bankroll"],
        initial_bankroll=stats["risk_state"]["initial_bankroll"],
        daily_pnl=stats["risk_state"]["daily_pnl"],
        total_pnl=stats["risk_state"]["total_pnl"],
        daily_trades=stats["risk_state"]["daily_trades"],
        total_trades=stats["risk_state"]["total_trades"],
        kill_switch_active=stats["risk_state"]["kill_switch_active"],
        scans_today=stats["daily_stats"]["scans_count"],
        opportunities_today=stats["daily_stats"]["opportunities_detected"],
        executed_today=stats["daily_stats"]["opportunities_executed"],
        mode=settings.mode if settings else "dry",
        scanner_running=scanner.is_running if scanner else False,
        last_scan=scanner.last_scan_time.isoformat() if scanner and scanner.last_scan_time else None,
    )


@app.get("/api/opportunities", response_model=list[OpportunityResponse])
async def get_opportunities(limit: int = 20):
    """Get recent opportunities."""
    repo = _dashboard_state.get("repository")

    if not repo:
        raise HTTPException(status_code=500, detail="Repository not initialized")

    opps = await repo.get_recent_opportunities(limit=limit)

    return [
        OpportunityResponse(
            id=opp.id,
            event_name=opp.event_name,
            sport=opp.sport,
            league=opp.league,
            edge=opp.edge,
            guaranteed_profit=opp.guaranteed_profit,
            roi=opp.roi,
            status=opp.status.value,
            detected_at=opp.detected_at.isoformat(),
            legs=opp.legs,
            requires_manual=opp.requires_manual,
        )
        for opp in opps
    ]


@app.post("/api/settings")
async def update_settings(settings: SettingsUpdate):
    """Update scanner settings."""
    app_settings = _dashboard_state.get("settings")
    repo = _dashboard_state.get("repository")

    if settings.mode and settings.mode in ("dry", "semi-auto", "auto"):
        app_settings.mode = settings.mode

    if settings.min_edge is not None:
        app_settings.risk.min_edge_percent = settings.min_edge

    if settings.max_stake_percent is not None:
        app_settings.risk.max_stake_percent = settings.max_stake_percent

    if settings.kill_switch is not None and repo:
        await repo.set_kill_switch(
            settings.kill_switch,
            "Manual activation" if settings.kill_switch else None
        )

    return {"status": "ok"}


@app.post("/api/scanner/start")
async def start_scanner():
    """Start the scanner."""
    scanner = _dashboard_state.get("scanner")
    if scanner:
        await scanner.start()
        return {"status": "started"}
    raise HTTPException(status_code=500, detail="Scanner not initialized")


@app.post("/api/scanner/stop")
async def stop_scanner():
    """Stop the scanner."""
    scanner = _dashboard_state.get("scanner")
    if scanner:
        await scanner.stop()
        return {"status": "stopped"}
    raise HTTPException(status_code=500, detail="Scanner not initialized")


# =========================================================================
# HTML Dashboard
# =========================================================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AutoBet Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/htmx.org@1.9.10"></script>
    <style>
        .animate-pulse-slow { animation: pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
        .gradient-bg { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); }
    </style>
</head>
<body class="gradient-bg min-h-screen text-white">
    <div class="container mx-auto px-4 py-8">
        <!-- Header -->
        <header class="mb-8">
            <h1 class="text-4xl font-bold mb-2">🤖 AutoBet Scanner</h1>
            <p class="text-gray-400">Real-time sports arbitrage detection</p>
        </header>

        <!-- Stats Cards -->
        <div id="stats-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8"
             hx-get="/api/stats" hx-trigger="load, every 5s" hx-swap="innerHTML">
            <div class="bg-gray-800 rounded-lg p-4 animate-pulse">Loading...</div>
        </div>

        <!-- Main Content Grid -->
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
            <!-- Opportunities Table -->
            <div class="lg:col-span-2 bg-gray-800 rounded-lg p-6">
                <h2 class="text-xl font-semibold mb-4">📊 Recent Opportunities</h2>
                <div id="opportunities-table"
                     hx-get="/api/opportunities?limit=10"
                     hx-trigger="load, every 10s"
                     hx-swap="innerHTML">
                    <div class="animate-pulse">Loading opportunities...</div>
                </div>
            </div>

            <!-- Control Panel -->
            <div class="bg-gray-800 rounded-lg p-6">
                <h2 class="text-xl font-semibold mb-4">⚙️ Control Panel</h2>

                <!-- Mode Selector -->
                <div class="mb-6">
                    <label class="block text-sm text-gray-400 mb-2">Execution Mode</label>
                    <select id="mode-select" class="w-full bg-gray-700 rounded px-3 py-2"
                            onchange="updateMode(this.value)">
                        <option value="dry">🔍 Dry Run (Detect Only)</option>
                        <option value="semi-auto">⚠️ Semi-Auto (Confirm)</option>
                        <option value="auto">🤖 Auto (Full Execution)</option>
                    </select>
                </div>

                <!-- Kill Switch -->
                <div class="mb-6">
                    <label class="block text-sm text-gray-400 mb-2">Kill Switch</label>
                    <button id="kill-switch-btn"
                            class="w-full bg-red-600 hover:bg-red-700 rounded px-4 py-2 font-semibold"
                            onclick="toggleKillSwitch()">
                        🛑 Activate Kill Switch
                    </button>
                </div>

                <!-- Scanner Control -->
                <div class="mb-6">
                    <label class="block text-sm text-gray-400 mb-2">Scanner</label>
                    <div class="flex gap-2">
                        <button class="flex-1 bg-green-600 hover:bg-green-700 rounded px-4 py-2"
                                onclick="startScanner()">▶️ Start</button>
                        <button class="flex-1 bg-yellow-600 hover:bg-yellow-700 rounded px-4 py-2"
                                onclick="stopScanner()">⏸️ Stop</button>
                    </div>
                </div>

                <!-- Risk Settings -->
                <div class="border-t border-gray-700 pt-4 mt-4">
                    <h3 class="text-sm font-semibold text-gray-400 mb-3">Risk Settings</h3>

                    <div class="mb-3">
                        <label class="text-xs text-gray-500">Min Edge %</label>
                        <input type="number" id="min-edge" value="0.8" step="0.1" min="0"
                               class="w-full bg-gray-700 rounded px-3 py-1 text-sm">
                    </div>

                    <div class="mb-3">
                        <label class="text-xs text-gray-500">Max Stake %</label>
                        <input type="number" id="max-stake" value="2" step="0.5" min="0" max="100"
                               class="w-full bg-gray-700 rounded px-3 py-1 text-sm">
                    </div>

                    <button class="w-full bg-blue-600 hover:bg-blue-700 rounded px-4 py-2 text-sm"
                            onclick="saveSettings()">
                        💾 Save Settings
                    </button>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Format stats into cards
        document.body.addEventListener('htmx:afterSwap', function(evt) {
            if (evt.detail.target.id === 'stats-container') {
                const data = JSON.parse(evt.detail.xhr.response);
                evt.detail.target.innerHTML = formatStatsCards(data);
            }
            if (evt.detail.target.id === 'opportunities-table') {
                const data = JSON.parse(evt.detail.xhr.response);
                evt.detail.target.innerHTML = formatOpportunitiesTable(data);
            }
        });

        function formatStatsCards(data) {
            const pnlColor = data.daily_pnl >= 0 ? 'text-green-400' : 'text-red-400';
            const killSwitchStatus = data.kill_switch_active
                ? '<span class="text-red-400">🔴 ACTIVE</span>'
                : '<span class="text-green-400">🟢 OFF</span>';

            return `
                <div class="bg-gray-800/50 backdrop-blur rounded-lg p-4 border border-gray-700">
                    <div class="text-gray-400 text-sm">💰 Bankroll</div>
                    <div class="text-2xl font-bold">${data.current_bankroll.toFixed(2)}€</div>
                    <div class="text-xs text-gray-500">Initial: ${data.initial_bankroll.toFixed(2)}€</div>
                </div>
                <div class="bg-gray-800/50 backdrop-blur rounded-lg p-4 border border-gray-700">
                    <div class="text-gray-400 text-sm">📈 Daily PnL</div>
                    <div class="text-2xl font-bold ${pnlColor}">${data.daily_pnl >= 0 ? '+' : ''}${data.daily_pnl.toFixed(2)}€</div>
                    <div class="text-xs text-gray-500">Total: ${data.total_pnl >= 0 ? '+' : ''}${data.total_pnl.toFixed(2)}€</div>
                </div>
                <div class="bg-gray-800/50 backdrop-blur rounded-lg p-4 border border-gray-700">
                    <div class="text-gray-400 text-sm">🎯 Opportunities</div>
                    <div class="text-2xl font-bold">${data.opportunities_today}</div>
                    <div class="text-xs text-gray-500">Executed: ${data.executed_today}</div>
                </div>
                <div class="bg-gray-800/50 backdrop-blur rounded-lg p-4 border border-gray-700">
                    <div class="text-gray-400 text-sm">🔒 Status</div>
                    <div class="text-lg font-bold">${killSwitchStatus}</div>
                    <div class="text-xs text-gray-500">Mode: ${data.mode.toUpperCase()}</div>
                </div>
            `;
        }

        function formatOpportunitiesTable(data) {
            if (!data || data.length === 0) {
                return '<div class="text-gray-500 text-center py-8">No opportunities detected yet</div>';
            }

            const rows = data.map(opp => {
                const statusBadge = {
                    'detected': '<span class="bg-blue-600 px-2 py-1 rounded text-xs">Detected</span>',
                    'executed': '<span class="bg-green-600 px-2 py-1 rounded text-xs">Executed</span>',
                    'failed': '<span class="bg-red-600 px-2 py-1 rounded text-xs">Failed</span>',
                    'partial': '<span class="bg-yellow-600 px-2 py-1 rounded text-xs">Partial</span>',
                    'expired': '<span class="bg-gray-600 px-2 py-1 rounded text-xs">Expired</span>',
                }[opp.status] || opp.status;

                const legsInfo = opp.legs.map(l =>
                    `${l.bookmaker}: ${l.odds.toFixed(2)}`
                ).join(' | ');

                return `
                    <tr class="border-b border-gray-700 hover:bg-gray-700/50">
                        <td class="py-3 px-2">
                            <div class="font-medium">${opp.event_name}</div>
                            <div class="text-xs text-gray-400">${opp.league}</div>
                        </td>
                        <td class="py-3 px-2 text-center">
                            <span class="text-green-400 font-bold">${opp.edge.toFixed(2)}%</span>
                        </td>
                        <td class="py-3 px-2 text-center">
                            <span class="font-medium">${opp.guaranteed_profit.toFixed(2)}€</span>
                        </td>
                        <td class="py-3 px-2 text-center">${statusBadge}</td>
                        <td class="py-3 px-2 text-xs text-gray-400">${legsInfo}</td>
                    </tr>
                `;
            }).join('');

            return `
                <table class="w-full">
                    <thead>
                        <tr class="text-left text-gray-400 text-sm border-b border-gray-700">
                            <th class="pb-2">Event</th>
                            <th class="pb-2 text-center">Edge</th>
                            <th class="pb-2 text-center">Profit</th>
                            <th class="pb-2 text-center">Status</th>
                            <th class="pb-2">Bookmakers</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            `;
        }

        // API functions
        async function updateMode(mode) {
            await fetch('/api/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({mode})
            });
        }

        async function toggleKillSwitch() {
            const btn = document.getElementById('kill-switch-btn');
            const isActive = btn.textContent.includes('Deactivate');
            await fetch('/api/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({kill_switch: !isActive})
            });
            btn.textContent = isActive ? '🛑 Activate Kill Switch' : '✅ Deactivate Kill Switch';
            btn.className = isActive
                ? 'w-full bg-red-600 hover:bg-red-700 rounded px-4 py-2 font-semibold'
                : 'w-full bg-green-600 hover:bg-green-700 rounded px-4 py-2 font-semibold';
        }

        async function startScanner() {
            await fetch('/api/scanner/start', {method: 'POST'});
        }

        async function stopScanner() {
            await fetch('/api/scanner/stop', {method: 'POST'});
        }

        async function saveSettings() {
            const minEdge = parseFloat(document.getElementById('min-edge').value);
            const maxStake = parseFloat(document.getElementById('max-stake').value) / 100;
            await fetch('/api/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({min_edge: minEdge, max_stake_percent: maxStake})
            });
            alert('Settings saved!');
        }
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the dashboard HTML."""
    return DASHBOARD_HTML


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}

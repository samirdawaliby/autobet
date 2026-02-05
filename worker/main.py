"""
Cloudflare Worker for AutoBet Scanner.

This worker runs on Cloudflare's edge network and:
1. Fetches odds from The Odds API on a cron schedule
2. Detects arbitrage opportunities
3. Sends alerts via Telegram
4. Stores results in D1 (SQLite) or KV

Deployment:
    wrangler deploy
"""
import json
from datetime import datetime, timezone
from js import Response, fetch, Object


# ============================================================================
# Configuration
# ============================================================================

# Bookmakers to scan (customize as needed)
BOOKMAKERS = [
    "pinnacle", "bet365", "1xbet", "unibet", "williamhill",
    "betfair_ex_eu", "smarkets", "matchbook",
    "marathonbet", "betvictor", "ladbrokes", "bwin"
]

# Sports to scan
SPORTS = ["tennis_atp", "tennis_wta", "soccer_epl", "soccer_uefa_champs_league"]

# Minimum edge to alert (percentage)
MIN_EDGE = 0.8

# Exchange commissions
EXCHANGE_COMMISSIONS = {
    "betfair_ex_eu": 0.05,
    "smarkets": 0.02,
    "matchbook": 0.02,
}

EXCHANGES = set(EXCHANGE_COMMISSIONS.keys())


# ============================================================================
# Odds Fetching
# ============================================================================

async def fetch_odds(api_key: str, sport: str) -> dict:
    """Fetch odds from The Odds API."""
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
    params = {
        "apiKey": api_key,
        "regions": "eu,uk",
        "markets": "h2h",
        "oddsFormat": "decimal",
        "bookmakers": ",".join(BOOKMAKERS),
    }

    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    full_url = f"{url}?{query_string}"

    response = await fetch(full_url)

    if response.status != 200:
        raise Exception(f"API error: {response.status}")

    data = await response.json()
    remaining = response.headers.get("x-requests-remaining", "unknown")

    return {
        "events": data,
        "remaining_requests": remaining,
    }


# ============================================================================
# Arbitrage Detection
# ============================================================================

def detect_arbitrage(events: list, min_edge: float = MIN_EDGE) -> list:
    """Detect arbitrage opportunities from odds data."""
    opportunities = []

    for event in events:
        event_id = event.get("id", "")
        home_team = event.get("home_team", "")
        away_team = event.get("away_team", "")
        bookmakers_data = event.get("bookmakers", [])

        if len(bookmakers_data) < 2:
            continue

        # Collect best odds for each outcome
        best_home = {"odds": 0, "bookmaker": None}
        best_away = {"odds": 0, "bookmaker": None}

        for bookie in bookmakers_data:
            bookie_name = bookie.get("key", "")
            markets = bookie.get("markets", [])

            for market in markets:
                if market.get("key") != "h2h":
                    continue

                for outcome in market.get("outcomes", []):
                    name = outcome.get("name", "")
                    odds = outcome.get("price", 0)

                    if name == home_team and odds > best_home["odds"]:
                        best_home = {"odds": odds, "bookmaker": bookie_name, "name": name}
                    elif name == away_team and odds > best_away["odds"]:
                        best_away = {"odds": odds, "bookmaker": bookie_name, "name": name}

        # Check for arbitrage
        if best_home["odds"] > 0 and best_away["odds"] > 0:
            # Apply commission for exchanges
            eff_home = apply_commission(best_home["odds"], best_home["bookmaker"])
            eff_away = apply_commission(best_away["odds"], best_away["bookmaker"])

            implied_sum = (1 / eff_home) + (1 / eff_away)

            if implied_sum < 1:
                edge = (1 - implied_sum) * 100

                if edge >= min_edge:
                    # Calculate stakes (for 100 base)
                    total_stake = 100
                    stake_home = (total_stake * (1 / eff_home)) / implied_sum
                    stake_away = (total_stake * (1 / eff_away)) / implied_sum

                    profit_home = stake_home * best_home["odds"] - total_stake
                    profit_away = stake_away * best_away["odds"] - total_stake
                    guaranteed_profit = min(profit_home, profit_away)

                    opportunities.append({
                        "event_id": event_id,
                        "event_name": f"{home_team} vs {away_team}",
                        "sport": event.get("sport_title", ""),
                        "commence_time": event.get("commence_time", ""),
                        "edge": round(edge, 2),
                        "implied_sum": round(implied_sum, 4),
                        "guaranteed_profit": round(guaranteed_profit, 2),
                        "legs": [
                            {
                                "selection": "home",
                                "name": best_home["name"],
                                "bookmaker": best_home["bookmaker"],
                                "odds": best_home["odds"],
                                "stake": round(stake_home, 2),
                                "is_exchange": best_home["bookmaker"] in EXCHANGES,
                            },
                            {
                                "selection": "away",
                                "name": best_away["name"],
                                "bookmaker": best_away["bookmaker"],
                                "odds": best_away["odds"],
                                "stake": round(stake_away, 2),
                                "is_exchange": best_away["bookmaker"] in EXCHANGES,
                            },
                        ],
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                    })

    # Sort by edge
    opportunities.sort(key=lambda x: x["edge"], reverse=True)
    return opportunities


def apply_commission(odds: float, bookmaker: str) -> float:
    """Apply exchange commission to odds."""
    if bookmaker in EXCHANGES:
        commission = EXCHANGE_COMMISSIONS.get(bookmaker, 0.05)
        return 1 + (odds - 1) * (1 - commission)
    return odds


# ============================================================================
# Telegram Alerts
# ============================================================================

async def send_telegram_alert(bot_token: str, chat_id: str, opportunity: dict) -> bool:
    """Send arbitrage alert to Telegram."""
    legs_text = "\n".join([
        f"  {'✅' if leg['is_exchange'] else '📍'} {leg['bookmaker']}: "
        f"{leg['name']} @ {leg['odds']:.2f} → {leg['stake']:.2f}€"
        for leg in opportunity["legs"]
    ])

    exec_type = "🤖 AUTO" if all(leg["is_exchange"] for leg in opportunity["legs"]) else "⚠️ SEMI-AUTO"
    if not any(leg["is_exchange"] for leg in opportunity["legs"]):
        exec_type = "📢 MANUAL"

    message = f"""
{exec_type} | Edge: {opportunity['edge']:.2f}%

📊 {opportunity['event_name']}
🏆 {opportunity['sport']}

{legs_text}

💰 Stake: 100€
💵 Profit: {opportunity['guaranteed_profit']:.2f}€
    """.strip()

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    response = await fetch(url, **{
        "method": "POST",
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }),
    })

    return response.status == 200


# ============================================================================
# Worker Handlers
# ============================================================================

async def on_fetch(request, env):
    """Handle HTTP requests (for dashboard/API)."""
    url = request.url
    path = url.split("/")[-1] if "/" in url else ""

    # Health check
    if path == "health" or path == "":
        return Response.new(
            json.dumps({
                "status": "healthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "service": "autobet-scanner",
            }),
            **{"headers": {"Content-Type": "application/json"}}
        )

    # Manual scan trigger
    if path == "scan":
        result = await run_scan(env)
        return Response.new(
            json.dumps(result),
            **{"headers": {"Content-Type": "application/json"}}
        )

    # Get recent opportunities from KV
    if path == "opportunities":
        data = await env.AUTOBET_KV.get("recent_opportunities")
        return Response.new(
            data or "[]",
            **{"headers": {"Content-Type": "application/json"}}
        )

    # Stats
    if path == "stats":
        data = await env.AUTOBET_KV.get("stats")
        return Response.new(
            data or "{}",
            **{"headers": {"Content-Type": "application/json"}}
        )

    return Response.new("Not found", **{"status": 404})


async def on_scheduled(event, env, ctx):
    """Handle scheduled (cron) events."""
    print(f"Cron triggered at {datetime.now(timezone.utc).isoformat()}")
    result = await run_scan(env)
    print(f"Scan complete: {result}")


async def run_scan(env) -> dict:
    """Run a complete scan cycle."""
    api_key = env.ODDS_API_KEY
    telegram_token = getattr(env, "TELEGRAM_BOT_TOKEN", None)
    telegram_chat = getattr(env, "TELEGRAM_CHAT_ID", None)

    if not api_key:
        return {"error": "ODDS_API_KEY not configured"}

    all_opportunities = []
    events_scanned = 0
    remaining_requests = "unknown"

    # Scan each sport
    for sport in SPORTS:
        try:
            result = await fetch_odds(api_key, sport)
            events = result["events"]
            remaining_requests = result["remaining_requests"]

            events_scanned += len(events)

            # Detect arbitrage
            opps = detect_arbitrage(events)
            all_opportunities.extend(opps)

            print(f"Scanned {sport}: {len(events)} events, {len(opps)} opportunities")

        except Exception as e:
            print(f"Error scanning {sport}: {e}")

    # Send alerts for opportunities
    alerts_sent = 0
    if telegram_token and telegram_chat:
        for opp in all_opportunities[:5]:  # Limit to top 5
            try:
                success = await send_telegram_alert(telegram_token, telegram_chat, opp)
                if success:
                    alerts_sent += 1
            except Exception as e:
                print(f"Telegram error: {e}")

    # Store results in KV
    try:
        # Store recent opportunities
        await env.AUTOBET_KV.put(
            "recent_opportunities",
            json.dumps(all_opportunities[:20]),
            **{"expirationTtl": 3600}  # 1 hour TTL
        )

        # Update stats
        stats_raw = await env.AUTOBET_KV.get("stats")
        stats = json.loads(stats_raw) if stats_raw else {
            "total_scans": 0,
            "total_opportunities": 0,
            "total_events": 0,
        }

        stats["total_scans"] += 1
        stats["total_opportunities"] += len(all_opportunities)
        stats["total_events"] += events_scanned
        stats["last_scan"] = datetime.now(timezone.utc).isoformat()
        stats["remaining_requests"] = remaining_requests

        await env.AUTOBET_KV.put("stats", json.dumps(stats))

    except Exception as e:
        print(f"KV storage error: {e}")

    return {
        "events_scanned": events_scanned,
        "opportunities_found": len(all_opportunities),
        "alerts_sent": alerts_sent,
        "remaining_requests": remaining_requests,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# Export handlers for Cloudflare
def on_fetch_handler(request, env):
    """Sync wrapper for fetch handler."""
    import asyncio
    return asyncio.run(on_fetch(request, env))


def on_scheduled_handler(event, env, ctx):
    """Sync wrapper for scheduled handler."""
    import asyncio
    asyncio.run(on_scheduled(event, env, ctx))

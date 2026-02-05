"""
The Odds API client - Primary odds source.
https://the-odds-api.com/

Covers 40+ bookmakers including:
- Pinnacle, Bet365, 1xBet, Unibet, William Hill
- Betfair Exchange, Smarkets, Matchbook
- And many more regional bookmakers
"""
import httpx
import structlog
from datetime import datetime, timezone
from typing import Optional

from .base import Event, Market, OddsMarket, OddsSelection, OddsSnapshot, OddsSource, Sport

logger = structlog.get_logger()


# Sport key mapping for The Odds API
SPORT_MAPPING = {
    Sport.TENNIS: "tennis_atp",  # Also tennis_wta, tennis_itf, etc.
    Sport.SOCCER: "soccer_epl",  # Also soccer_spain_la_liga, etc.
    Sport.BASKETBALL: "basketball_nba",
    Sport.AMERICAN_FOOTBALL: "americanfootball_nfl",
    Sport.ICE_HOCKEY: "icehockey_nhl",
    Sport.MMA: "mma_mixed_martial_arts",
    Sport.BOXING: "boxing_boxing",
}

# Extended sport keys for comprehensive coverage
EXTENDED_SPORT_KEYS = {
    Sport.TENNIS: [
        "tennis_atp",
        "tennis_wta",
        "tennis_itf_men",
        "tennis_itf_women",
    ],
    Sport.SOCCER: [
        "soccer_epl",
        "soccer_spain_la_liga",
        "soccer_germany_bundesliga",
        "soccer_italy_serie_a",
        "soccer_france_ligue_one",
        "soccer_uefa_champs_league",
        "soccer_uefa_europa_league",
    ],
}


class TheOddsAPI(OddsSource):
    """
    Client for The Odds API.

    Free tier: 500 requests/month
    Covers: 40+ bookmakers, 70+ sports
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.the-odds-api.com/v4",
        bookmakers: list[str] | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.bookmakers = bookmakers
        self._client: Optional[httpx.AsyncClient] = None
        self._remaining_requests: Optional[int] = None

    @property
    def name(self) -> str:
        return "the_odds_api"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30.0,
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_sports(self) -> list[dict]:
        """Get list of available sports."""
        client = await self._get_client()
        response = await client.get(
            "/sports",
            params={"apiKey": self.api_key},
        )
        response.raise_for_status()
        return response.json()

    async def fetch_odds(
        self,
        sport: Sport,
        markets: list[Market],
        regions: list[str] | None = None,
        bookmakers: list[str] | None = None,
    ) -> OddsSnapshot:
        """
        Fetch odds for a sport from multiple bookmakers.

        Args:
            sport: Sport to fetch
            markets: Market types (h2h, spreads, totals)
            regions: Regions to include (eu, uk, us, au)
            bookmakers: Specific bookmakers (overrides self.bookmakers)

        Returns:
            OddsSnapshot with normalized event data
        """
        client = await self._get_client()

        # Default regions for maximum coverage
        if regions is None:
            regions = ["eu", "uk", "us", "au"]

        # Use provided bookmakers or default
        bookie_filter = bookmakers or self.bookmakers

        # Get sport keys to query
        sport_keys = EXTENDED_SPORT_KEYS.get(sport, [SPORT_MAPPING.get(sport)])

        all_events: list[Event] = []

        for sport_key in sport_keys:
            if not sport_key:
                continue

            try:
                params = {
                    "apiKey": self.api_key,
                    "regions": ",".join(regions),
                    "markets": ",".join(m.value for m in markets),
                    "oddsFormat": "decimal",
                }

                if bookie_filter:
                    params["bookmakers"] = ",".join(bookie_filter)

                response = await client.get(f"/sports/{sport_key}/odds", params=params)

                # Track remaining requests from headers
                self._remaining_requests = int(
                    response.headers.get("x-requests-remaining", 0)
                )

                if response.status_code == 404:
                    logger.warning("sport_not_found", sport_key=sport_key)
                    continue

                response.raise_for_status()
                data = response.json()

                # Parse events
                events = self._parse_events(data, sport)
                all_events.extend(events)

                logger.info(
                    "odds_fetched",
                    source=self.name,
                    sport_key=sport_key,
                    event_count=len(events),
                    remaining_requests=self._remaining_requests,
                )

            except httpx.HTTPStatusError as e:
                logger.error(
                    "fetch_error",
                    source=self.name,
                    sport_key=sport_key,
                    status_code=e.response.status_code,
                    error=str(e),
                )
            except Exception as e:
                logger.error(
                    "fetch_error",
                    source=self.name,
                    sport_key=sport_key,
                    error=str(e),
                )

        return OddsSnapshot(
            source_name=self.name,
            timestamp=datetime.now(timezone.utc),
            events=all_events,
            remaining_requests=self._remaining_requests,
        )

    def _parse_events(self, data: list[dict], sport: Sport) -> list[Event]:
        """Parse raw API response into Event objects."""
        events = []

        for event_data in data:
            try:
                event = Event(
                    event_id=event_data["id"],
                    sport=sport,
                    league=event_data.get("sport_title", ""),
                    home_team=event_data["home_team"],
                    away_team=event_data["away_team"],
                    commence_time=datetime.fromisoformat(
                        event_data["commence_time"].replace("Z", "+00:00")
                    ),
                    markets={},
                )

                # Parse bookmakers and their odds
                for bookmaker_data in event_data.get("bookmakers", []):
                    bookmaker_name = bookmaker_data["key"]
                    last_update = datetime.fromisoformat(
                        bookmaker_data["last_update"].replace("Z", "+00:00")
                    )

                    event.markets[bookmaker_name] = {}

                    for market_data in bookmaker_data.get("markets", []):
                        market_key = market_data["key"]

                        try:
                            market_type = Market(market_key)
                        except ValueError:
                            continue  # Skip unsupported markets

                        selections = []
                        for outcome in market_data.get("outcomes", []):
                            selections.append(
                                OddsSelection(
                                    name=outcome["name"],
                                    odds=float(outcome["price"]),
                                    bookmaker=bookmaker_name,
                                    timestamp=last_update,
                                )
                            )

                        event.markets[bookmaker_name][market_key] = OddsMarket(
                            market_type=market_type,
                            selections=selections,
                        )

                events.append(event)

            except Exception as e:
                logger.warning(
                    "parse_event_error",
                    event_id=event_data.get("id"),
                    error=str(e),
                )

        return events

    async def fetch_all_sports_odds(
        self,
        sports: list[Sport],
        markets: list[Market],
    ) -> OddsSnapshot:
        """
        Fetch odds for multiple sports at once.

        Args:
            sports: List of sports to fetch
            markets: Market types to fetch

        Returns:
            Combined OddsSnapshot
        """
        all_events = []

        for sport in sports:
            snapshot = await self.fetch_odds(sport, markets)
            all_events.extend(snapshot.events)

        return OddsSnapshot(
            source_name=self.name,
            timestamp=datetime.now(timezone.utc),
            events=all_events,
            remaining_requests=self._remaining_requests,
        )

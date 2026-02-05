"""
Database models for AutoBet.
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, String, Text, Enum as SQLEnum
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class OpportunityStatus(str, Enum):
    """Status of an arbitrage opportunity."""
    DETECTED = "detected"
    EXECUTING = "executing"
    EXECUTED = "executed"
    PARTIAL = "partial"
    FAILED = "failed"
    EXPIRED = "expired"
    SKIPPED = "skipped"


class OrderStatus(str, Enum):
    """Status of a bet order."""
    PENDING = "pending"
    PLACED = "placed"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Opportunity(Base):
    """
    Detected arbitrage opportunity.
    """
    __tablename__ = "opportunities"

    id = Column(String, primary_key=True)
    event_id = Column(String, index=True)
    event_name = Column(String)
    sport = Column(String)
    league = Column(String)
    market = Column(String)
    commence_time = Column(DateTime)

    # Arbitrage metrics
    edge = Column(Float)
    implied_probability_sum = Column(Float)
    total_stake = Column(Float)
    guaranteed_profit = Column(Float)
    roi = Column(Float)

    # Legs stored as JSON
    legs = Column(JSON)

    # Execution info
    executable_legs = Column(Integer)
    requires_manual = Column(Boolean)

    # Status tracking
    status = Column(SQLEnum(OpportunityStatus), default=OpportunityStatus.DETECTED)
    detected_at = Column(DateTime, default=datetime.utcnow)
    executed_at = Column(DateTime, nullable=True)
    actual_profit = Column(Float, nullable=True)

    # Metadata
    bookmaker_count = Column(Integer)
    min_odds_age_seconds = Column(Float)
    max_odds_age_seconds = Column(Float)


class Order(Base):
    """
    Bet order placed on an exchange.
    """
    __tablename__ = "orders"

    id = Column(String, primary_key=True)
    opportunity_id = Column(String, index=True)
    leg_index = Column(Integer)

    # Order details
    exchange = Column(String)
    event_id = Column(String)
    selection = Column(String)
    selection_name = Column(String)

    # Pricing
    requested_odds = Column(Float)
    filled_odds = Column(Float, nullable=True)
    requested_stake = Column(Float)
    filled_stake = Column(Float, nullable=True)

    # Status
    status = Column(SQLEnum(OrderStatus), default=OrderStatus.PENDING)
    exchange_order_id = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    filled_at = Column(DateTime, nullable=True)


class RiskState(Base):
    """
    Current risk management state.
    Single row table - tracks global risk metrics.
    """
    __tablename__ = "risk_state"

    id = Column(Integer, primary_key=True, default=1)

    # Bankroll
    initial_bankroll = Column(Float)
    current_bankroll = Column(Float)

    # Daily metrics (reset daily)
    daily_stake = Column(Float, default=0)
    daily_pnl = Column(Float, default=0)
    daily_trades = Column(Integer, default=0)
    daily_wins = Column(Integer, default=0)

    # Cumulative metrics
    total_stake = Column(Float, default=0)
    total_pnl = Column(Float, default=0)
    total_trades = Column(Integer, default=0)
    total_wins = Column(Integer, default=0)

    # Kill switch
    kill_switch_active = Column(Boolean, default=False)
    kill_switch_reason = Column(String, nullable=True)

    # Timestamps
    last_trade_at = Column(DateTime, nullable=True)
    daily_reset_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OddsHistory(Base):
    """
    Historical odds snapshots for backtesting.
    """
    __tablename__ = "odds_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String, index=True)
    bookmaker = Column(String)
    selection = Column(String)
    odds = Column(Float)
    timestamp = Column(DateTime, index=True)

    # Event info (denormalized for query efficiency)
    sport = Column(String)
    league = Column(String)
    home_team = Column(String)
    away_team = Column(String)


class DailyStats(Base):
    """
    Daily aggregated statistics.
    """
    __tablename__ = "daily_stats"

    date = Column(String, primary_key=True)  # YYYY-MM-DD

    # Scan stats
    scans_count = Column(Integer, default=0)
    events_scanned = Column(Integer, default=0)
    opportunities_detected = Column(Integer, default=0)

    # Execution stats
    opportunities_executed = Column(Integer, default=0)
    opportunities_partial = Column(Integer, default=0)
    opportunities_failed = Column(Integer, default=0)

    # Financial stats
    total_stake = Column(Float, default=0)
    total_pnl = Column(Float, default=0)
    best_edge = Column(Float, default=0)
    avg_edge = Column(Float, default=0)

    # Performance
    win_rate = Column(Float, default=0)
    roi = Column(Float, default=0)

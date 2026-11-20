"""All tunables in one place. Override with environment variables."""
import os
from dataclasses import dataclass, field


def _f(name, default):
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Config:
    base_url: str = os.environ.get("KALSHI_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2")
    key_id: str = os.environ.get("KALSHI_KEY_ID", "")
    private_key_path: str = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
    db_path: str = os.environ.get("KALSHI_DB", "data/bot.sqlite")
    calibration_path: str = os.environ.get("KALSHI_CALIBRATION", "data/calibration.json")

    # --- Hard risk limits (dollars) ---
    daily_budget: float = _f("KALSHI_DAILY_BUDGET", 5.0)
    weekly_budget: float = _f("KALSHI_WEEKLY_BUDGET", 35.0)
    max_per_bet: float = _f("KALSHI_MAX_PER_BET", 1.0)
    max_bets_per_day: int = int(_f("KALSHI_MAX_BETS_PER_DAY", 5))

    # --- Tail strategy ---
    max_tail_price_c: int = int(_f("KALSHI_MAX_TAIL_PRICE", 15))   # only buy sides priced <= this (cents)
    min_tail_price_c: int = int(_f("KALSHI_MIN_TAIL_PRICE", 2))    # 1c contracts: fee rounding kills them
    min_volume_24h: int = int(_f("KALSHI_MIN_VOLUME_24H", 200))
    max_hours_to_close: float = _f("KALSHI_MAX_HOURS_TO_CLOSE", 72)
    min_edge_c: float = _f("KALSHI_MIN_EDGE", 1.0)                 # required edge after fees, cents/contract
    min_bucket_samples: int = int(_f("KALSHI_MIN_BUCKET_SAMPLES", 300))
    prior_strength: float = _f("KALSHI_PRIOR_STRENGTH", 200)       # shrink toward "market is right"

    # --- Calibration snapshot ---
    snapshot_hours_before_close: float = _f("KALSHI_SNAPSHOT_HOURS", 24)

    timezone: str = os.environ.get("KALSHI_TZ", "America/New_York")


CONFIG = Config()

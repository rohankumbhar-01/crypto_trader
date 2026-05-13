"""
Feature Engineering
===================
Converts a single DataFrame row (after compute_indicators) into a flat
feature dict that LightGBM can train on and predict from.

All features are numeric. Categorical signals are encoded as integers.

Feature groups:
  - Momentum   : RSI, MACD histogram, MACD crossover flag
  - Trend      : EMA ratios, trend direction, price vs EMA levels
  - Volatility : ATR%, BB width, BB position, candle body/wick ratios
  - Volume     : volume spike flag, volume vs SMA ratio
  - Composite  : price vs VWAP, price change %, RSI zone
"""

import numpy as np
import pandas as pd
from frappe.utils import flt


# Feature column order — must be identical at train and predict time
FEATURE_COLUMNS = [
	# Momentum
	"rsi",
	"rsi_zone",          # 0=oversold(<30), 1=neutral, 2=overbought(>70)
	"macd_hist",
	"macd_cross_up",     # 1 if macd crossed above signal this candle
	"macd_cross_down",   # 1 if macd crossed below signal this candle
	# Trend
	"trend_direction",   # 1=bullish, -1=bearish, 0=mixed
	"price_vs_ema20",    # (close - ema20) / ema20 * 100
	"price_vs_ema50",
	"price_vs_ema200",
	"ema20_vs_ema50",    # (ema20 - ema50) / ema50 * 100
	"ema50_vs_ema200",
	# Volatility
	"atr_pct",           # atr / close * 100
	"bb_width",          # (upper - lower) / middle * 100
	"bb_position",       # (close - lower) / (upper - lower) — 0=at lower, 1=at upper
	"candle_body_pct",   # |close - open| / open * 100
	"upper_wick_pct",
	"lower_wick_pct",
	# Volume
	"volume_spike",      # 1 if volume > 2× SMA20
	"volume_ratio",      # volume / volume_sma20
	# Composite
	"price_vs_vwap",     # (close - vwap) / vwap * 100
	"price_change_pct",  # (close - prev_close) / prev_close * 100 — passed in as field
]


def extract_features(row: pd.Series) -> dict:
	"""
	Extract the feature vector from a single indicator-enriched DataFrame row.

	row: a pandas Series with all indicator columns present
	Returns: dict keyed by FEATURE_COLUMNS
	"""
	close  = flt(row.get("close"))
	open_  = flt(row.get("open"))
	high   = flt(row.get("high"))
	low    = flt(row.get("low"))

	ema20  = flt(row.get("ema20"))
	ema50  = flt(row.get("ema50"))
	ema200 = flt(row.get("ema200"))
	vwap   = flt(row.get("vwap"))
	atr    = flt(row.get("atr"))

	bb_upper  = flt(row.get("bb_upper"))
	bb_lower  = flt(row.get("bb_lower"))
	bb_middle = flt(row.get("bb_middle", (bb_upper + bb_lower) / 2 if bb_upper and bb_lower else 0))

	vol     = flt(row.get("volume"))
	vol_sma = flt(row.get("volume_sma20"))

	macd        = flt(row.get("macd"))
	macd_signal = flt(row.get("macd_signal"))
	macd_hist   = flt(row.get("macd_hist"))

	rsi = flt(row.get("rsi"))

	# --- derived features ---

	# RSI zone
	if rsi < 30:
		rsi_zone = 0
	elif rsi > 70:
		rsi_zone = 2
	else:
		rsi_zone = 1

	# MACD crossover — requires prev row values; use sign of hist as proxy
	# hist > 0 and was <= 0 → cross up; approximated by sign change flag stored in row
	# During training the df has consecutive rows, so we embed this as a binary from hist sign
	macd_cross_up   = 1 if macd_hist > 0 and macd > macd_signal else 0
	macd_cross_down = 1 if macd_hist < 0 and macd < macd_signal else 0

	# Price vs EMA levels (percentage distance)
	price_vs_ema20  = _safe_pct(close - ema20, ema20)
	price_vs_ema50  = _safe_pct(close - ema50, ema50)
	price_vs_ema200 = _safe_pct(close - ema200, ema200)
	ema20_vs_ema50  = _safe_pct(ema20 - ema50, ema50)
	ema50_vs_ema200 = _safe_pct(ema50 - ema200, ema200)

	# ATR as % of close
	atr_pct = _safe_pct(atr, close)

	# Bollinger position — 0 at lower band, 1 at upper band
	bb_range = bb_upper - bb_lower
	bb_position = _safe_div(close - bb_lower, bb_range)

	# BB width
	bb_width = _safe_pct(bb_upper - bb_lower, bb_middle)

	# Candle anatomy
	body = abs(close - open_)
	upper_wick = high - max(open_, close)
	lower_wick = min(open_, close) - low
	candle_body_pct = _safe_pct(body, open_)
	upper_wick_pct  = _safe_pct(upper_wick, open_)
	lower_wick_pct  = _safe_pct(lower_wick, open_)

	# Volume ratio
	volume_ratio = _safe_div(vol, vol_sma) if vol_sma else 0.0

	# Price vs VWAP
	price_vs_vwap = _safe_pct(close - vwap, vwap)

	# Price change — dataset_builder stores this as field if available
	price_change_pct = flt(row.get("price_change_pct", 0.0))

	return {
		"rsi":              round(rsi, 4),
		"rsi_zone":         rsi_zone,
		"macd_hist":        round(macd_hist, 6),
		"macd_cross_up":    macd_cross_up,
		"macd_cross_down":  macd_cross_down,
		"trend_direction":  int(row.get("trend_direction", 0)),
		"price_vs_ema20":   round(price_vs_ema20, 4),
		"price_vs_ema50":   round(price_vs_ema50, 4),
		"price_vs_ema200":  round(price_vs_ema200, 4),
		"ema20_vs_ema50":   round(ema20_vs_ema50, 4),
		"ema50_vs_ema200":  round(ema50_vs_ema200, 4),
		"atr_pct":          round(atr_pct, 4),
		"bb_width":         round(bb_width, 4),
		"bb_position":      round(bb_position, 4),
		"candle_body_pct":  round(candle_body_pct, 4),
		"upper_wick_pct":   round(upper_wick_pct, 4),
		"lower_wick_pct":   round(lower_wick_pct, 4),
		"volume_spike":     int(row.get("volume_spike", 0)),
		"volume_ratio":     round(volume_ratio, 4),
		"price_vs_vwap":    round(price_vs_vwap, 4),
		"price_change_pct": round(price_change_pct, 4),
	}


def features_from_snapshot(snapshot: dict) -> dict:
	"""
	Build a feature dict from a get_market_snapshot() result dict.
	Used at prediction time — snapshot already has all indicator values.
	"""
	row = pd.Series(snapshot)

	# Reconstruct bb_middle from snapshot if not present
	if "bb_middle" not in row and "bb_upper" in row and "bb_lower" in row:
		row["bb_middle"] = (row["bb_upper"] + row["bb_lower"]) / 2

	return extract_features(row)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _safe_pct(numerator: float, denominator: float) -> float:
	if not denominator:
		return 0.0
	return numerator / denominator * 100


def _safe_div(numerator: float, denominator: float) -> float:
	if not denominator:
		return 0.0
	return numerator / denominator

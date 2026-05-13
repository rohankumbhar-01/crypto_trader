"""
Market Data + Indicator Engine
==============================
Responsibilities:
  1. Fetch OHLCV candles from CoinDCX (with Redis caching)
  2. Normalise raw candle dicts into a clean DataFrame
  3. Compute all technical indicators required by the ML pipeline
  4. Return a feature-ready dict for a single candle (latest row)

Indicators computed:
  RSI(14)          — momentum oscillator
  MACD(12,26,9)    — trend/momentum
  EMA20            — short-term trend
  EMA50            — mid-term trend
  EMA200           — long-term trend
  ATR(14)          — volatility / stoploss sizing
  VWAP             — intraday smart-money reference
  Bollinger Bands  — volatility expansion detection
  Volume Spike     — breakout signal (vol > 2× 20-period avg)
  Trend direction  — derived from EMA stack
"""

import json

import frappe
import pandas as pd
from frappe import _
from frappe.utils import cint, flt, now_datetime

import ta
import ta.momentum
import ta.trend
import ta.volatility
import ta.volume

from coin_trader.exchange import get_candles, symbol_to_pair

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CACHE_TTL_SEC = 60          # candle cache lifetime in Redis
_MIN_CANDLES = 210           # need ≥210 rows to compute EMA200 reliably
_MAX_CANDLES = 500           # CoinDCX hard limit per request


# ---------------------------------------------------------------------------
# Candle fetching
# ---------------------------------------------------------------------------

def fetch_candles(symbol: str, interval: str, limit: int = _MAX_CANDLES) -> list[dict]:
	"""
	Fetch OHLCV candles for a symbol.  Results are Redis-cached for _CACHE_TTL_SEC.

	symbol:   CoinDCX symbol e.g. 'BTCINR'
	interval: candle interval e.g. '15m', '1h', '1d'
	Returns list of raw candle dicts [{open, high, low, close, volume, time}, ...]
	"""
	cache_key = f"ct_candles:{symbol}:{interval}:{limit}"
	cached = frappe.cache().get_value(cache_key)
	if cached:
		return json.loads(cached)

	pair = symbol_to_pair(symbol)
	candles = get_candles(pair, interval, limit=min(limit, _MAX_CANDLES))

	if candles:
		frappe.cache().set_value(cache_key, json.dumps(candles), expires_in_sec=_CACHE_TTL_SEC)

	return candles


def candles_to_dataframe(candles: list[dict]) -> pd.DataFrame:
	"""
	Convert raw CoinDCX candle list to a clean DataFrame.

	Columns: open, high, low, close, volume, time (datetime index)
	Sorted oldest → newest.
	"""
	if not candles:
		return pd.DataFrame()

	df = pd.DataFrame(candles)

	# Normalise column names — CoinDCX returns lowercase already
	df = df.rename(columns={
		"open": "open",
		"high": "high",
		"low": "low",
		"close": "close",
		"volume": "volume",
		"time": "time",
	})

	required = {"open", "high", "low", "close", "volume", "time"}
	if not required.issubset(df.columns):
		missing = required - set(df.columns)
		frappe.throw(_("Candle data missing columns: {0}").format(", ".join(missing)))

	df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
	df = df.set_index("time").sort_index()

	for col in ("open", "high", "low", "close", "volume"):
		df[col] = pd.to_numeric(df[col], errors="coerce")

	df = df.dropna(subset=["open", "high", "low", "close", "volume"])
	return df


# ---------------------------------------------------------------------------
# Indicator engine
# ---------------------------------------------------------------------------

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
	"""
	Compute all technical indicators on the OHLCV DataFrame in-place.
	Requires at least _MIN_CANDLES rows for EMA200 to be meaningful.

	Adds columns:
	  rsi, macd, macd_signal, macd_hist,
	  ema20, ema50, ema200,
	  atr, vwap,
	  bb_upper, bb_middle, bb_lower, bb_width,
	  volume_sma20, volume_spike,
	  trend_direction
	"""
	if df.empty:
		return df

	close = df["close"]
	high  = df["high"]
	low   = df["low"]
	vol   = df["volume"]

	# RSI (14)
	df["rsi"] = ta.momentum.RSIIndicator(close=close, window=14).rsi()

	# MACD (12, 26, 9)
	macd_obj = ta.trend.MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
	df["macd"]        = macd_obj.macd()
	df["macd_signal"] = macd_obj.macd_signal()
	df["macd_hist"]   = macd_obj.macd_diff()

	# EMA 20 / 50 / 200
	df["ema20"]  = ta.trend.EMAIndicator(close=close, window=20).ema_indicator()
	df["ema50"]  = ta.trend.EMAIndicator(close=close, window=50).ema_indicator()
	df["ema200"] = ta.trend.EMAIndicator(close=close, window=200).ema_indicator()

	# ATR (14)
	df["atr"] = ta.volatility.AverageTrueRange(
		high=high, low=low, close=close, window=14
	).average_true_range()

	# VWAP — requires intraday index; compute as rolling(20) VWAP approximation
	# when daily candles are used, VWAP = (H+L+C)/3 weighted by volume rolling sum
	typical_price = (high + low + close) / 3
	df["vwap"] = (
		(typical_price * vol).rolling(window=20).sum()
		/ vol.rolling(window=20).sum()
	)

	# Bollinger Bands (20, 2σ)
	bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
	df["bb_upper"]  = bb.bollinger_hband()
	df["bb_middle"] = bb.bollinger_mavg()
	df["bb_lower"]  = bb.bollinger_lband()
	# Band width as % of middle — measures expansion/contraction
	df["bb_width"]  = (df["bb_upper"] - df["bb_lower"]) / df["bb_middle"] * 100

	# Volume spike — current vol > 2× 20-period rolling average
	df["volume_sma20"] = vol.rolling(window=20).mean()
	df["volume_spike"] = (vol > df["volume_sma20"] * 2).astype(int)

	# Trend direction — derived from EMA stack alignment
	# 1 = bullish (price > EMA20 > EMA50 > EMA200)
	# -1 = bearish (price < EMA20 < EMA50 < EMA200)
	# 0 = mixed
	def _trend(row):
		if pd.isna(row["ema200"]):
			return 0
		if row["close"] > row["ema20"] > row["ema50"] > row["ema200"]:
			return 1
		if row["close"] < row["ema20"] < row["ema50"] < row["ema200"]:
			return -1
		return 0

	df["trend_direction"] = df.apply(_trend, axis=1)

	return df


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------

def get_market_snapshot(symbol: str, interval: str = "15m") -> dict:
	"""
	Fetch candles, compute indicators, and return the latest candle's
	feature snapshot as a flat dict ready for ML prediction input.

	Returns {} if not enough data is available.
	"""
	candles = fetch_candles(symbol, interval)
	if len(candles) < _MIN_CANDLES:
		frappe.log_error(
			title="Insufficient candle data",
			message=f"{symbol} {interval}: got {len(candles)}, need {_MIN_CANDLES}",
		)
		return {}

	df = candles_to_dataframe(candles)
	if df.empty:
		return {}

	df = compute_indicators(df)

	# Return the latest complete row as a flat dict
	latest = df.iloc[-1]
	prev   = df.iloc[-2]

	return {
		"symbol":           symbol,
		"interval":         interval,
		"timestamp":        str(df.index[-1]),
		# Price
		"open":             flt(latest["open"]),
		"high":             flt(latest["high"]),
		"low":              flt(latest["low"]),
		"close":            flt(latest["close"]),
		"volume":           flt(latest["volume"]),
		# Momentum
		"rsi":              flt(latest["rsi"], 4),
		"macd":             flt(latest["macd"], 6),
		"macd_signal":      flt(latest["macd_signal"], 6),
		"macd_hist":        flt(latest["macd_hist"], 6),
		# Trend
		"ema20":            flt(latest["ema20"], 2),
		"ema50":            flt(latest["ema50"], 2),
		"ema200":           flt(latest["ema200"], 2),
		"trend_direction":  cint(latest["trend_direction"]),
		# Volatility
		"atr":              flt(latest["atr"], 2),
		"bb_upper":         flt(latest["bb_upper"], 2),
		"bb_middle":        flt(latest["bb_middle"], 2),
		"bb_lower":         flt(latest["bb_lower"], 2),
		"bb_width":         flt(latest["bb_width"], 4),
		# Volume
		"vwap":             flt(latest["vwap"], 2),
		"volume_sma20":     flt(latest["volume_sma20"], 4),
		"volume_spike":     cint(latest["volume_spike"]),
		# Price vs key levels (for ML features)
		"price_vs_ema20":   flt((latest["close"] - latest["ema20"]) / latest["ema20"] * 100, 4),
		"price_vs_ema50":   flt((latest["close"] - latest["ema50"]) / latest["ema50"] * 100, 4),
		"price_vs_vwap":    flt((latest["close"] - latest["vwap"]) / latest["vwap"] * 100, 4),
		# Candle body and wick ratios (useful ML features)
		"candle_body_pct":  flt(abs(latest["close"] - latest["open"]) / latest["open"] * 100, 4),
		"upper_wick_pct":   flt((latest["high"] - max(latest["open"], latest["close"])) / latest["open"] * 100, 4),
		"lower_wick_pct":   flt((min(latest["open"], latest["close"]) - latest["low"]) / latest["open"] * 100, 4),
		# 1-period price change
		"price_change_pct": flt((latest["close"] - prev["close"]) / prev["close"] * 100, 4),
	}


def get_multi_timeframe_snapshot(symbol: str, intervals: list[str] | None = None) -> dict:
	"""
	Fetch snapshots across multiple timeframes and merge into one dict.
	Useful for multi-timeframe feature sets in ML training.

	intervals default: ['15m', '1h', '4h']
	Keys are prefixed with the interval: e.g. 'rsi_15m', 'rsi_1h'.
	"""
	if intervals is None:
		intervals = ["15m", "1h", "4h"]

	combined = {"symbol": symbol}
	for interval in intervals:
		snap = get_market_snapshot(symbol, interval)
		if not snap:
			continue
		for key, val in snap.items():
			if key == "symbol":
				continue
			combined[f"{key}_{interval}"] = val

	return combined


@frappe.whitelist()
def get_snapshot_api(symbol: str, interval: str = "15m") -> dict:
	"""Whitelisted — called from dashboard JS for live indicator display."""
	return get_market_snapshot(symbol, interval)

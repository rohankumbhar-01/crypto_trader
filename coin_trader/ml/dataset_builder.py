"""
Dataset Builder
===============
Collects historical OHLCV candles for a list of symbols and intervals,
computes indicators via the market_data engine, generates BUY/SELL/HOLD
labels based on future return, and returns a clean DataFrame ready for
LightGBM training.

Label logic:
  future_return = (close[t+n] - close[t]) / close[t]

  BUY  → future_return >  +threshold
  SELL → future_return <  -threshold
  HOLD → |future_return| <= threshold

Default: lookahead=3 candles, threshold=0.005 (0.5%)
"""

import frappe
import pandas as pd
import numpy as np
from frappe import _

from coin_trader.market_data import fetch_candles, candles_to_dataframe, compute_indicators
from coin_trader.ml.feature_engineering import extract_features


# Label encoding — LightGBM expects integers
LABEL_MAP  = {"BUY": 2, "SELL": 0, "HOLD": 1}
LABEL_RMAP = {2: "BUY", 0: "SELL", 1: "HOLD"}


def build_dataset(
	symbols: list[str],
	interval: str = "1h",
	lookahead: int = 3,
	threshold: float = 0.005,
	limit: int = 500,
) -> pd.DataFrame:
	"""
	Build a labelled feature DataFrame for training.

	symbols:   list of CoinDCX symbols e.g. ['BTCINR', 'ETHINR']
	interval:  candle timeframe
	lookahead: how many candles ahead to compute future return
	threshold: minimum % move to label as BUY or SELL (0.005 = 0.5%)
	limit:     candles to fetch per symbol (max 500 from CoinDCX)

	Returns DataFrame with feature columns + 'label' (int) + 'label_str' (str).
	"""
	all_rows = []

	for symbol in symbols:
		try:
			rows = _build_for_symbol(symbol, interval, lookahead, threshold, limit)
			if rows is not None and not rows.empty:
				all_rows.append(rows)
		except Exception as e:
			frappe.log_error(
				title=f"Dataset builder error: {symbol}",
				message=str(e),
			)

	if not all_rows:
		return pd.DataFrame()

	df = pd.concat(all_rows, ignore_index=True)
	df = df.dropna()
	return df


def _build_for_symbol(
	symbol: str,
	interval: str,
	lookahead: int,
	threshold: float,
	limit: int,
) -> pd.DataFrame | None:
	"""Build labelled rows for a single symbol."""
	candles = fetch_candles(symbol, interval, limit=limit)
	if not candles:
		return None

	df = candles_to_dataframe(candles)
	if df.empty or len(df) < 210:
		return None

	df = compute_indicators(df)

	# Generate labels using future return
	df["future_close"]  = df["close"].shift(-lookahead)
	df["future_return"] = (df["future_close"] - df["close"]) / df["close"]

	def _label(ret):
		if pd.isna(ret):
			return np.nan
		if ret > threshold:
			return LABEL_MAP["BUY"]
		if ret < -threshold:
			return LABEL_MAP["SELL"]
		return LABEL_MAP["HOLD"]

	df["label"] = df["future_return"].apply(_label)

	# Drop rows with NaN labels (last `lookahead` rows have no future data)
	df = df.dropna(subset=["label"])
	df["label"] = df["label"].astype(int)
	df["label_str"] = df["label"].map(LABEL_RMAP)
	df["symbol"] = symbol

	# Extract feature vector for each row
	feature_rows = []
	for idx, row in df.iterrows():
		features = extract_features(row)
		features["label"]     = row["label"]
		features["label_str"] = row["label_str"]
		features["symbol"]    = symbol
		features["timestamp"] = str(idx)
		feature_rows.append(features)

	return pd.DataFrame(feature_rows)


def get_label_distribution(df: pd.DataFrame) -> dict:
	"""Return BUY/SELL/HOLD counts and percentages for a dataset."""
	if df.empty or "label_str" not in df.columns:
		return {}
	counts = df["label_str"].value_counts().to_dict()
	total  = len(df)
	return {
		k: {"count": v, "pct": round(v / total * 100, 1)}
		for k, v in counts.items()
	}

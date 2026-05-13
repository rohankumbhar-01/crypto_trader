"""
LightGBM Inference
==================
Loads a trained model from disk and returns a structured probability
prediction for a given symbol + interval.

Output structure (matches CT ML Prediction DocType):
  {
    "prediction":          "BUY" | "SELL" | "HOLD",
    "upward_probability":  float (0-100),
    "downward_probability":float (0-100),
    "expected_move_pct":   float,
    "confidence_score":    int (0-100),
    "confidence_level":    "Very High" | "High" | "Medium" | "Weak" | "Ignore",
    "features_json":       str  (JSON snapshot of input features),
    "model_version":       str,
  }
"""

import json

import frappe
import numpy as np
from frappe import _
from frappe.utils import now_datetime

import lightgbm as lgb

from coin_trader.market_data import get_market_snapshot
from coin_trader.ml.feature_engineering import FEATURE_COLUMNS, features_from_snapshot
from coin_trader.ml.train_model import _model_path, get_model_meta, model_exists
from coin_trader.ml.dataset_builder import LABEL_RMAP


# ---------------------------------------------------------------------------
# Confidence mapping
# ---------------------------------------------------------------------------

def _confidence_level(score: int) -> str:
	if score >= 90:
		return "Very High"
	if score >= 80:
		return "High"
	if score >= 70:
		return "Medium"
	if score >= 60:
		return "Weak"
	return "Ignore"


# ---------------------------------------------------------------------------
# Model loader (process-level cache)
# ---------------------------------------------------------------------------

_model_cache: dict[str, lgb.Booster] = {}


def _load_model(user: str) -> lgb.Booster:
	"""Load and cache the Booster for a user."""
	path = _model_path(user)
	if user not in _model_cache:
		if not model_exists(user):
			frappe.throw(
				_("No trained ML model found for user {0}. Run training first.").format(user)
			)
		_model_cache[user] = lgb.Booster(model_file=path)
	return _model_cache[user]


def invalidate_model_cache(user: str) -> None:
	"""Call this after retraining so the next predict loads the new model."""
	_model_cache.pop(user, None)


# ---------------------------------------------------------------------------
# Core prediction
# ---------------------------------------------------------------------------

def predict(user: str, symbol: str, interval: str = "1h") -> dict:
	"""
	Run ML inference for a symbol and return a structured prediction dict.

	Steps:
	  1. Fetch latest market snapshot (indicators computed)
	  2. Extract feature vector
	  3. Run LightGBM Booster.predict() → class probabilities
	  4. Map to BUY/SELL/HOLD with confidence scores
	  5. Save result to CT ML Prediction DocType
	  6. Return structured dict
	"""
	# 1. Market snapshot
	snapshot = get_market_snapshot(symbol, interval)
	if not snapshot:
		return {}

	# 2. Feature vector
	features = features_from_snapshot(snapshot)
	X = np.array([[features[col] for col in FEATURE_COLUMNS]], dtype=np.float32)

	# 3. Inference
	booster = _load_model(user)
	# Returns shape (1, 3) — probabilities for [SELL=0, HOLD=1, BUY=2]
	proba = booster.predict(X)[0]

	sell_prob = float(proba[0])
	hold_prob = float(proba[1])
	buy_prob  = float(proba[2])

	# 4. Determine prediction and confidence
	predicted_class = int(np.argmax(proba))
	prediction_str  = LABEL_RMAP[predicted_class]
	max_prob        = float(np.max(proba))

	# Confidence score: max class probability mapped to 0-100
	confidence_score = int(round(max_prob * 100))
	confidence_level = _confidence_level(confidence_score)

	# Upward/downward probability
	# upward = BUY probability; downward = SELL probability
	# We normalise so upward + downward = 100
	total_directional = buy_prob + sell_prob
	if total_directional > 0:
		upward_pct   = round(buy_prob  / total_directional * 100, 2)
		downward_pct = round(sell_prob / total_directional * 100, 2)
	else:
		upward_pct   = 50.0
		downward_pct = 50.0

	# Expected move: ATR-based estimate, signed by direction
	atr_pct = float(snapshot.get("atr_pct", 0)) or _estimate_atr_pct(snapshot)
	if prediction_str == "BUY":
		expected_move = round(atr_pct * (buy_prob - sell_prob), 4)
	elif prediction_str == "SELL":
		expected_move = round(-atr_pct * (sell_prob - buy_prob), 4)
	else:
		expected_move = 0.0

	# 5. Model metadata
	meta = get_model_meta(user)
	model_version = meta.get("model_version", "unknown")

	result = {
		"prediction":           prediction_str,
		"upward_probability":   upward_pct,
		"downward_probability": downward_pct,
		"hold_probability":     round(hold_prob * 100, 2),
		"expected_move_pct":    expected_move,
		"confidence_score":     confidence_score,
		"confidence_level":     confidence_level,
		"features_json":        json.dumps(features),
		"model_version":        model_version,
		"symbol":               symbol,
		"interval":             interval,
		"close":                snapshot.get("close"),
		"atr":                  snapshot.get("atr"),
		"rsi":                  snapshot.get("rsi"),
		"trend_direction":      snapshot.get("trend_direction"),
		"volume_spike":         snapshot.get("volume_spike"),
		# carry snapshot forward so probability_engine can reuse it
		"_snapshot":            snapshot,
	}

	# 6. Persist to CT ML Prediction
	_save_prediction(user, symbol, result)

	return result


def _estimate_atr_pct(snapshot: dict) -> float:
	"""Fallback ATR% estimate if not present in snapshot."""
	close = snapshot.get("close") or 1
	atr   = snapshot.get("atr") or 0
	return round(atr / close * 100, 4) if close else 0.0


def _save_prediction(user: str, symbol: str, result: dict) -> None:
	"""Insert a CT ML Prediction record."""
	try:
		doc = frappe.get_doc({
			"doctype":            "CT ML Prediction",
			"user":               user,
			"symbol":             symbol,
			"prediction":         result["prediction"],
			"upward_probability": result["upward_probability"],
			"downward_probability": result["downward_probability"],
			"expected_move_pct":  result["expected_move_pct"],
			"confidence_score":   result["confidence_score"],
			"confidence_level":   result["confidence_level"],
			"features_json":      result["features_json"],
			"model_version":      result["model_version"],
			"prediction_time":    now_datetime(),
		})
		doc.flags.ignore_permissions = True
		doc.insert()
		frappe.db.commit()
	except Exception as e:
		frappe.log_error(title="CT ML Prediction save failed", message=str(e))


# ---------------------------------------------------------------------------
# Batch prediction
# ---------------------------------------------------------------------------

def predict_all(user: str, symbols: list[str], interval: str = "1h") -> list[dict]:
	"""
	Run predictions for multiple symbols.
	Skips symbols with insufficient data or below minimum confidence.
	Returns only predictions where confidence_score >= 60.
	"""
	results = []
	for symbol in symbols:
		try:
			result = predict(user, symbol, interval)
			if result and result.get("confidence_score", 0) >= 60:
				results.append(result)
		except Exception as e:
			frappe.log_error(
				title=f"Prediction failed: {symbol}",
				message=str(e),
			)
	return results


# ---------------------------------------------------------------------------
# Whitelisted — called from dashboard JS
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_prediction_api(symbol: str, interval: str = "1h") -> dict:
	"""Run ML prediction for the current session user."""
	if not model_exists(frappe.session.user):
		return {"error": "No trained model. Please wait for daily training or trigger manually."}
	return predict(frappe.session.user, symbol, interval)

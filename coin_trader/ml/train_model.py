"""
LightGBM Training Pipeline
===========================
Trains one LightGBM multi-class classifier per user.
Classes: BUY=2, HOLD=1, SELL=0

Model lifecycle:
  - Stored at: coin_trader/ml/models/{user}/model.lgb
  - Metadata stored at: coin_trader/ml/models/{user}/meta.json
  - Re-trained daily at 2 AM via tasks.daily_ml_retrain()
  - Training data: all active symbols from CT Trading Config, default interval

Training strategy:
  - TimeSeriesSplit(5) cross-validation to avoid lookahead bias
  - Class-weight balanced to handle BUY/HOLD/SELL imbalance
  - Early stopping on validation logloss
  - Feature importance logged to Frappe Error Log for monitoring
"""

import json
import os
import time

import frappe
import numpy as np
import pandas as pd
from frappe import _
from frappe.utils import now_datetime

import lightgbm as lgb
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, classification_report
from sklearn.preprocessing import LabelEncoder

from coin_trader.ml.dataset_builder import build_dataset, get_label_distribution, LABEL_MAP
from coin_trader.ml.feature_engineering import FEATURE_COLUMNS

# ---------------------------------------------------------------------------
# Model storage
# ---------------------------------------------------------------------------

def _models_dir() -> str:
	"""Absolute path to the models directory inside the app."""
	return os.path.join(
		os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
		"ml",
		"models",
	)


def _user_model_dir(user: str) -> str:
	safe_user = user.replace("@", "_at_").replace(".", "_")
	path = os.path.join(_models_dir(), safe_user)
	os.makedirs(path, exist_ok=True)
	return path


def _model_path(user: str) -> str:
	return os.path.join(_user_model_dir(user), "model.lgb")


def _meta_path(user: str) -> str:
	return os.path.join(_user_model_dir(user), "meta.json")


def model_exists(user: str) -> bool:
	return os.path.isfile(_model_path(user))


def get_model_meta(user: str) -> dict:
	path = _meta_path(user)
	if not os.path.isfile(path):
		return {}
	with open(path) as f:
		return json.load(f)


# ---------------------------------------------------------------------------
# LightGBM hyperparameters
# ---------------------------------------------------------------------------

_LGB_PARAMS = {
	"objective":        "multiclass",
	"num_class":        3,
	"metric":           "multi_logloss",
	"boosting_type":    "gbdt",
	"num_leaves":       63,
	"max_depth":        -1,
	"learning_rate":    0.05,
	"n_estimators":     1000,
	"feature_fraction": 0.8,
	"bagging_fraction": 0.8,
	"bagging_freq":     5,
	"min_child_samples": 20,
	"lambda_l1":        0.1,
	"lambda_l2":        0.1,
	"verbose":          -1,
	"n_jobs":           -1,
	"random_state":     42,
	"class_weight":     "balanced",
}

_EARLY_STOPPING_ROUNDS = 50
_N_SPLITS = 5


# ---------------------------------------------------------------------------
# Main training function
# ---------------------------------------------------------------------------

def train(
	user: str,
	symbols: list[str],
	interval: str = "1h",
	lookahead: int = 3,
	threshold: float = 0.005,
) -> dict:
	"""
	Train (or retrain) the LightGBM model for a user.

	Returns a result dict with accuracy, label distribution, training time, etc.
	Saves the model to disk on success.
	"""
	start = time.time()
	frappe.log_error(
		title=f"ML Training started: {user}",
		message=f"symbols={symbols} interval={interval} lookahead={lookahead}",
	)

	# 1. Build dataset
	df = build_dataset(symbols, interval=interval, lookahead=lookahead, threshold=threshold)
	if df.empty or len(df) < 100:
		msg = f"Insufficient training data for {user}: {len(df)} rows"
		frappe.log_error(title="ML Training failed", message=msg)
		return {"success": False, "error": msg}

	dist = get_label_distribution(df)

	# 2. Prepare X / y
	X = df[FEATURE_COLUMNS].values.astype(np.float32)
	y = df["label"].values.astype(np.int32)

	# 3. TimeSeriesSplit cross-validation — respect temporal order
	tscv = TimeSeriesSplit(n_splits=_N_SPLITS)
	cv_accuracies = []

	for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
		X_train, X_val = X[train_idx], X[val_idx]
		y_train, y_val = y[train_idx], y[val_idx]

		model = lgb.LGBMClassifier(**_LGB_PARAMS)
		model.fit(
			X_train, y_train,
			eval_set=[(X_val, y_val)],
			callbacks=[
				lgb.early_stopping(_EARLY_STOPPING_ROUNDS, verbose=False),
				lgb.log_evaluation(period=-1),
			],
		)
		preds = model.predict(X_val)
		acc = accuracy_score(y_val, preds)
		cv_accuracies.append(acc)

	mean_cv_acc = float(np.mean(cv_accuracies))

	# 4. Final model trained on ALL data
	final_model = lgb.LGBMClassifier(**_LGB_PARAMS)
	final_model.fit(X, y, callbacks=[lgb.log_evaluation(period=-1)])

	# 5. Save model
	final_model.booster_.save_model(_model_path(user))

	# 6. Feature importance
	importance = dict(zip(
		FEATURE_COLUMNS,
		final_model.feature_importances_.tolist(),
	))
	top_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:10]

	# 7. Save metadata
	elapsed = round(time.time() - start, 1)
	meta = {
		"user":           user,
		"symbols":        symbols,
		"interval":       interval,
		"lookahead":      lookahead,
		"threshold":      threshold,
		"trained_at":     str(now_datetime()),
		"training_rows":  len(df),
		"cv_accuracy":    round(mean_cv_acc, 4),
		"cv_folds":       _N_SPLITS,
		"label_dist":     dist,
		"top_features":   top_features,
		"elapsed_sec":    elapsed,
		"model_version":  f"v{int(time.time())}",
	}
	with open(_meta_path(user), "w") as f:
		json.dump(meta, f, indent=2)

	frappe.log_error(
		title=f"ML Training complete: {user}",
		message=(
			f"rows={len(df)} cv_acc={mean_cv_acc:.3f} "
			f"elapsed={elapsed}s dist={dist}"
		),
	)

	return {"success": True, **meta}


# ---------------------------------------------------------------------------
# Frappe scheduler entry point
# ---------------------------------------------------------------------------

def daily_train() -> None:
	"""
	Called by Frappe scheduler at 2 AM daily.
	Trains a model for every user that has an active CT Trading Config.
	"""
	configs = frappe.get_all(
		"CT Trading Config",
		filters={"is_active": 1},
		fields=["user", "candle_interval"],
	)

	for cfg in configs:
		user = cfg["user"]
		interval = cfg.get("candle_interval") or "1h"

		# Collect enabled symbols from the child table
		symbols_rows = frappe.get_all(
			"CT Trading Symbol",
			filters={"parent": frappe.db.get_value("CT Trading Config", {"user": user}, "name"),
					 "enabled": 1},
			fields=["symbol"],
		)
		symbols = [r["symbol"] for r in symbols_rows]
		if not symbols:
			continue

		try:
			train(user=user, symbols=symbols, interval=interval)
		except Exception as e:
			frappe.log_error(
				title=f"ML daily_train failed: {user}",
				message=str(e),
			)

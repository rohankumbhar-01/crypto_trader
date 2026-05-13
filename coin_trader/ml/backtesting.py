"""
ML Backtesting Engine
=====================
Walk-forward historical replay of the ML + probability pipeline.

Strategy:
  For each candle in the historical window:
    1. Build feature vector from indicators up to that candle (no lookahead)
    2. Run LightGBM inference → prediction + probabilities
    3. Apply probability engine rules (composite score, context alignment)
    4. If signal passes the confidence threshold, simulate a trade:
       - Entry:    close of signal candle
       - Stoploss: entry ± ATR × 1.5
       - Target:   entry ± ATR × 3.0
       - Exit:     first candle where price crosses stoploss or target
                   (or end of window — counted as a timeout close at last close)
    5. Record trade result (PnL %, duration, exit reason)

Metrics computed:
  total_trades, win_rate, profit_factor, net_profit_pct,
  max_drawdown_pct, sharpe_ratio, avg_win_pct, avg_loss_pct,
  avg_duration_candles, expectancy_pct
"""

import numpy as np
import pandas as pd

import lightgbm as lgb

from coin_trader.ml.feature_engineering import FEATURE_COLUMNS, extract_features
from coin_trader.ml.dataset_builder import LABEL_RMAP
from coin_trader.ml.train_model import _model_path, model_exists


# ---------------------------------------------------------------------------
# Constants — mirror probability_engine.py
# ---------------------------------------------------------------------------

_STOPLOSS_ATR_MULT = 1.5
_TARGET_ATR_MULT   = 3.0
_W_ML  = 0.50
_W_CTX = 0.25
_W_MTF = 0.10   # MTF not available in backtest (single df) — weight redistributed
_W_VOL = 0.15


# ---------------------------------------------------------------------------
# Model loader (separate from predict.py cache — backtest uses its own)
# ---------------------------------------------------------------------------

def _load_booster(user: str) -> lgb.Booster:
    if not model_exists(user):
        raise FileNotFoundError(f"No trained model for user {user}")
    return lgb.Booster(model_file=_model_path(user))


# ---------------------------------------------------------------------------
# Feature extraction from a DataFrame row
# ---------------------------------------------------------------------------

def _row_to_features(row: pd.Series) -> np.ndarray:
    """
    Extract the 21-feature vector from a single DataFrame row.
    Returns shape (1, 21) float32 array.
    """
    feat = extract_features(row)
    return np.array([[feat[col] for col in FEATURE_COLUMNS]], dtype=np.float32)


# ---------------------------------------------------------------------------
# Signal builder — mirrors probability_engine._build_market_context
# ---------------------------------------------------------------------------

def _composite_score(row: pd.Series, prediction: str, ml_conf: float) -> float:
    """
    Lightweight composite score for backtesting.
    No MTF (single timeframe df) — volume weight absorbs it.
    """
    rsi        = float(row.get("rsi", 50))
    trend      = int(row.get("trend_direction", 0))
    macd_hist  = float(row.get("macd_hist", 0))
    price_vwap = float(row.get("price_vs_vwap", 0))
    vol_spike  = int(row.get("volume_spike", 0))
    vol_ratio  = float(row.get("volume_ratio", 1))

    if prediction == "BUY":
        rsi_ok   = rsi < 70
        trend_ok = trend >= 0
        macd_ok  = macd_hist > 0
        vwap_ok  = price_vwap > 0
    elif prediction == "SELL":
        rsi_ok   = rsi > 30
        trend_ok = trend <= 0
        macd_ok  = macd_hist < 0
        vwap_ok  = price_vwap < 0
    else:
        return 0.0

    aligned    = sum([rsi_ok, trend_ok, macd_ok, vwap_ok])
    ctx_score  = min(100, aligned * 25 + (10 if vol_spike else 0))

    if vol_spike:
        vol_score = 100.0
    else:
        vol_score = min(100, vol_ratio * 50)

    composite = (
        _W_ML  * ml_conf +
        _W_CTX * ctx_score +
        _W_VOL * vol_score
    )
    # rescale: total weights now 0.50+0.25+0.15 = 0.90 → normalise to 100
    return min(100.0, composite / 0.90)


# ---------------------------------------------------------------------------
# Trade simulator — exit on SL/target/timeout
# ---------------------------------------------------------------------------

def _simulate_trade(
    df: pd.DataFrame,
    entry_idx: int,
    prediction: str,
    atr: float,
) -> dict | None:
    """
    Simulate a trade entered at df.iloc[entry_idx].
    Scans subsequent candles for exit conditions.

    Returns a trade dict, or None if entry is invalid.
    """
    entry_price = float(df.iloc[entry_idx]["close"])
    if not entry_price or not atr:
        return None

    if prediction == "BUY":
        stoploss = entry_price - atr * _STOPLOSS_ATR_MULT
        target   = entry_price + atr * _TARGET_ATR_MULT
    elif prediction == "SELL":
        stoploss = entry_price + atr * _STOPLOSS_ATR_MULT
        target   = entry_price - atr * _TARGET_ATR_MULT
    else:
        return None

    exit_price  = None
    exit_reason = "timeout"
    duration    = 0

    for i in range(entry_idx + 1, len(df)):
        row   = df.iloc[i]
        high  = float(row["high"])
        low   = float(row["low"])
        close = float(row["close"])
        duration = i - entry_idx

        if prediction == "BUY":
            if low <= stoploss:
                exit_price  = stoploss
                exit_reason = "stoploss"
                break
            if high >= target:
                exit_price  = target
                exit_reason = "target"
                break
        elif prediction == "SELL":
            if high >= stoploss:
                exit_price  = stoploss
                exit_reason = "stoploss"
                break
            if low <= target:
                exit_price  = target
                exit_reason = "target"
                break

        # Timeout after 20 candles
        if duration >= 20:
            exit_price  = close
            exit_reason = "timeout"
            break

    if exit_price is None:
        # Reached end of data
        exit_price  = float(df.iloc[-1]["close"])
        exit_reason = "timeout"
        duration    = len(df) - 1 - entry_idx

    if prediction == "BUY":
        pnl_pct = (exit_price - entry_price) / entry_price * 100
    else:
        pnl_pct = (entry_price - exit_price) / entry_price * 100

    return {
        "entry_idx":   entry_idx,
        "entry_price": entry_price,
        "exit_price":  exit_price,
        "exit_reason": exit_reason,
        "direction":   prediction,
        "pnl_pct":     round(pnl_pct, 4),
        "duration":    duration,
        "stoploss":    stoploss,
        "target":      target,
    }


# ---------------------------------------------------------------------------
# Metrics calculator
# ---------------------------------------------------------------------------

def _compute_metrics(trades: list[dict], initial_capital: float = 100_000.0) -> dict:
    """
    Compute performance metrics from a list of trade dicts.
    All PnL figures are in percent of entry capital.
    """
    if not trades:
        return {
            "total_trades":        0,
            "win_rate":            0.0,
            "profit_factor":       0.0,
            "net_profit_pct":      0.0,
            "max_drawdown_pct":    0.0,
            "sharpe_ratio":        0.0,
            "avg_win_pct":         0.0,
            "avg_loss_pct":        0.0,
            "avg_duration_candles": 0.0,
            "expectancy_pct":      0.0,
        }

    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_rate      = len(wins) / len(pnls) * 100
    gross_profit  = sum(wins) if wins else 0.0
    gross_loss    = abs(sum(losses)) if losses else 0.0
    profit_factor = round(gross_profit / gross_loss, 3) if gross_loss > 0 else 0.0
    net_profit    = sum(pnls)

    # Equity curve for drawdown
    equity = initial_capital
    peak   = initial_capital
    max_dd = 0.0
    for p in pnls:
        equity += equity * p / 100
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe ratio (annualised, assuming daily candles ~ 365 per year)
    pnl_arr = np.array(pnls)
    sharpe  = 0.0
    if len(pnl_arr) > 1 and pnl_arr.std() > 0:
        sharpe = round(float(pnl_arr.mean() / pnl_arr.std() * np.sqrt(365)), 3)

    avg_win  = round(float(np.mean(wins)), 4)  if wins   else 0.0
    avg_loss = round(float(np.mean(losses)), 4) if losses else 0.0
    avg_dur  = round(float(np.mean([t["duration"] for t in trades])), 1)

    # Expectancy = (win_rate × avg_win) + (loss_rate × avg_loss)
    loss_rate  = 1 - win_rate / 100
    expectancy = round((win_rate / 100 * avg_win) + (loss_rate * avg_loss), 4)

    return {
        "total_trades":         len(trades),
        "win_rate":             round(win_rate, 2),
        "profit_factor":        profit_factor,
        "net_profit_pct":       round(net_profit, 4),
        "max_drawdown_pct":     round(max_dd, 4),
        "sharpe_ratio":         sharpe,
        "avg_win_pct":          avg_win,
        "avg_loss_pct":         avg_loss,
        "avg_duration_candles": avg_dur,
        "expectancy_pct":       expectancy,
    }


# ---------------------------------------------------------------------------
# Walk-forward replay — main entry point
# ---------------------------------------------------------------------------

def run_backtest(
    user: str,
    df: pd.DataFrame,
    min_confidence: float = 70.0,
    initial_capital: float = 100_000.0,
) -> dict:
    """
    Run a walk-forward backtest on a pre-built OHLCV+indicator DataFrame.

    df must have columns produced by market_data.compute_indicators():
      open, high, low, close, volume, rsi, macd_hist, trend_direction,
      price_vs_vwap, volume_spike, volume_ratio, atr, and all FEATURE_COLUMNS.

    min_confidence: composite score threshold (same as CT Trading Config)
    initial_capital: starting INR for drawdown calculation

    Returns a metrics dict + trades list.
    """
    booster = _load_booster(user)

    trades        = []
    open_position = None  # one position at a time

    # Need at least 200 candles of warm-up for EMA200 to be meaningful
    warmup = 200

    for idx in range(warmup, len(df)):
        row = df.iloc[idx]

        # ── If we have an open position, check exit first ─────────────────
        if open_position:
            high  = float(row["high"])
            low   = float(row["low"])
            close = float(row["close"])
            direction = open_position["direction"]
            sl        = open_position["stoploss"]
            tgt       = open_position["target"]

            exited = False
            if direction == "BUY":
                if low <= sl:
                    open_position["exit_price"]  = sl
                    open_position["exit_reason"] = "stoploss"
                    exited = True
                elif high >= tgt:
                    open_position["exit_price"]  = tgt
                    open_position["exit_reason"] = "target"
                    exited = True
            elif direction == "SELL":
                if high >= sl:
                    open_position["exit_price"]  = sl
                    open_position["exit_reason"] = "stoploss"
                    exited = True
                elif low <= tgt:
                    open_position["exit_price"]  = tgt
                    open_position["exit_reason"] = "target"
                    exited = True

            # Timeout after 20 candles
            if not exited and (idx - open_position["entry_idx"]) >= 20:
                open_position["exit_price"]  = close
                open_position["exit_reason"] = "timeout"
                exited = True

            if exited:
                ep = open_position["entry_price"]
                xp = open_position["exit_price"]
                pnl = (xp - ep) / ep * 100 if direction == "BUY" else (ep - xp) / ep * 100
                open_position["pnl_pct"]  = round(pnl, 4)
                open_position["duration"] = idx - open_position["entry_idx"]
                trades.append(open_position)
                open_position = None

        # ── No open position — look for a new signal ──────────────────────
        if open_position is None:
            try:
                X     = _row_to_features(row)
                proba = booster.predict(X)[0]  # [sell, hold, buy]

                predicted_class = int(np.argmax(proba))
                prediction      = LABEL_RMAP[predicted_class]
                ml_conf         = float(np.max(proba)) * 100

                if prediction == "HOLD":
                    continue

                score = _composite_score(row, prediction, ml_conf)
                if score < min_confidence:
                    continue

                atr = float(row.get("atr", 0))
                if not atr:
                    continue

                entry_price = float(row["close"])
                if prediction == "BUY":
                    sl  = entry_price - atr * _STOPLOSS_ATR_MULT
                    tgt = entry_price + atr * _TARGET_ATR_MULT
                else:
                    sl  = entry_price + atr * _STOPLOSS_ATR_MULT
                    tgt = entry_price - atr * _TARGET_ATR_MULT

                open_position = {
                    "entry_idx":   idx,
                    "entry_price": entry_price,
                    "exit_price":  None,
                    "exit_reason": None,
                    "direction":   prediction,
                    "stoploss":    sl,
                    "target":      tgt,
                    "ml_conf":     round(ml_conf, 2),
                    "score":       round(score, 2),
                    "pnl_pct":     None,
                    "duration":    None,
                }
            except Exception:
                continue

    # Close any still-open position at end of data
    if open_position:
        ep  = open_position["entry_price"]
        xp  = float(df.iloc[-1]["close"])
        pnl = (xp - ep) / ep * 100 if open_position["direction"] == "BUY" else (ep - xp) / ep * 100
        open_position["exit_price"]  = xp
        open_position["exit_reason"] = "end_of_data"
        open_position["pnl_pct"]     = round(pnl, 4)
        open_position["duration"]    = len(df) - 1 - open_position["entry_idx"]
        trades.append(open_position)

    metrics = _compute_metrics(trades, initial_capital)
    return {**metrics, "trades": trades}

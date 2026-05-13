"""
Backtest Engine
===============
Orchestrates the full backtesting pipeline:
  1. Fetch historical candles for a symbol + interval
  2. Compute technical indicators on the full history
  3. Run walk-forward ML replay (ml/backtesting.py)
  4. Persist results to CT Backtest Result DocType
  5. Return metrics + trade log to the caller

Entry points:
  run_backtest()       — called programmatically (e.g. from tests or tasks)
  run_backtest_api()   — @frappe.whitelist(), called from desk

Limitations (by design):
  - Single symbol per run (multi-symbol aggregation is a future phase)
  - No slippage or fee model — PnL is gross
  - Backtest uses the currently trained model (not a model trained on
    the backtest period only) — this is intentional for simplicity;
    walk-forward prevents strict lookahead at the candle level
"""

import json

import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime

from coin_trader.market_data import fetch_candles, candles_to_dataframe, compute_indicators
from coin_trader.ml.backtesting import run_backtest as _run_walk_forward
from coin_trader.ml.train_model import model_exists


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_backtest(
    user: str,
    symbol: str,
    interval: str = "1h",
    limit: int = 1000,
    min_confidence: float = 70.0,
    initial_capital: float = 100_000.0,
    strategy_name: str = "",
) -> dict:
    """
    Run a full backtest for one symbol and persist the result.

    Returns:
    {
      "success":        bool,
      "backtest_name":  str,   # CT Backtest Result docname
      "metrics":        dict,  # performance metrics
      "trades":         list,  # individual trade records
      "error":          str,   # set on failure
    }
    """
    if not model_exists(user):
        return {"success": False, "error": "No trained model found. Run training first."}

    # 1. Fetch candles
    candles = fetch_candles(symbol, interval, limit=limit)
    if not candles or len(candles) < 250:
        return {
            "success": False,
            "error":   f"Insufficient candle data for {symbol} ({len(candles or [])} candles, need 250+)",
        }

    # 2. Build indicator DataFrame
    df = candles_to_dataframe(candles)
    df = compute_indicators(df)
    df = df.dropna().reset_index(drop=True)

    if len(df) < 220:
        return {
            "success": False,
            "error":   f"After indicator warmup only {len(df)} candles remain — need 220+",
        }

    from_date = str(df.index[0])   if hasattr(df.index[0],  "date") else str(df.iloc[0].get("datetime",  ""))
    to_date   = str(df.index[-1])  if hasattr(df.index[-1], "date") else str(df.iloc[-1].get("datetime", ""))

    # Use DatetimeIndex if available
    try:
        from_date = df.index[0].date().isoformat()
        to_date   = df.index[-1].date().isoformat()
    except Exception:
        pass

    # 3. Walk-forward replay
    result = _run_walk_forward(
        user=user,
        df=df,
        min_confidence=min_confidence,
        initial_capital=initial_capital,
    )

    metrics = {k: v for k, v in result.items() if k != "trades"}
    trades  = result.get("trades", [])

    name = strategy_name or f"{symbol} {interval} backtest"

    # 4. Persist to CT Backtest Result
    bt_name = _save_result(
        user=user,
        strategy_name=name,
        symbol=symbol,
        interval=interval,
        from_date=from_date,
        to_date=to_date,
        candles_used=len(df),
        min_confidence=min_confidence,
        initial_capital=initial_capital,
        metrics=metrics,
        trades=trades,
    )

    return {
        "success":       True,
        "backtest_name": bt_name,
        "metrics":       metrics,
        "trades":        trades,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _save_result(
    user: str,
    strategy_name: str,
    symbol: str,
    interval: str,
    from_date: str,
    to_date: str,
    candles_used: int,
    min_confidence: float,
    initial_capital: float,
    metrics: dict,
    trades: list,
) -> str:
    """Insert a CT Backtest Result record and return its docname."""
    net_profit_inr = round(initial_capital * metrics.get("net_profit_pct", 0) / 100, 2)

    notes_lines = [
        f"Symbol: {symbol} | Interval: {interval}",
        f"Candles: {candles_used} | Min confidence: {min_confidence}%",
        f"Initial capital: ₹{initial_capital:,.0f}",
        f"Avg duration: {metrics.get('avg_duration_candles', 0):.1f} candles",
        f"Expectancy: {metrics.get('expectancy_pct', 0):.4f}%",
        f"Trades breakdown — SL exits / Target hits / Timeouts:",
    ]
    sl_count  = sum(1 for t in trades if t.get("exit_reason") == "stoploss")
    tgt_count = sum(1 for t in trades if t.get("exit_reason") == "target")
    to_count  = sum(1 for t in trades if t.get("exit_reason") in ("timeout", "end_of_data"))
    notes_lines.append(f"  SL={sl_count}  Target={tgt_count}  Timeout={to_count}")

    try:
        doc = frappe.get_doc({
            "doctype":        "CT Backtest Result",
            "user":           user,
            "strategy_name":  strategy_name,
            "tested_from":    from_date,
            "tested_to":      to_date,
            "total_trades":   metrics.get("total_trades", 0),
            "win_rate":       metrics.get("win_rate", 0.0),
            "profit_factor":  metrics.get("profit_factor", 0.0),
            "net_profit":     net_profit_inr,
            "max_drawdown":   metrics.get("max_drawdown_pct", 0.0),
            "sharpe_ratio":   metrics.get("sharpe_ratio", 0.0),
            "avg_win_pct":    metrics.get("avg_win_pct", 0.0),
            "avg_loss_pct":   metrics.get("avg_loss_pct", 0.0),
            "notes":          "\n".join(notes_lines),
        })
        doc.flags.ignore_permissions = True
        doc.insert()
        frappe.db.commit()
        return doc.name
    except Exception as e:
        frappe.log_error(title="Backtest: CT Backtest Result save failed", message=str(e))
        return ""


# ---------------------------------------------------------------------------
# Whitelisted — desk + dashboard
# ---------------------------------------------------------------------------

@frappe.whitelist()
def run_backtest_api(
    symbol: str,
    interval: str = "1h",
    limit: int = 1000,
    min_confidence: float = 70.0,
    initial_capital: float = 100_000.0,
    strategy_name: str = "",
) -> dict:
    """
    Trigger a backtest from the Frappe desk or dashboard.
    Runs for the current session user.
    Returns metrics + first 50 trades (full list can be large).
    """
    user = frappe.session.user

    result = run_backtest(
        user=user,
        symbol=symbol,
        interval=interval,
        limit=cint(limit),
        min_confidence=flt(min_confidence),
        initial_capital=flt(initial_capital),
        strategy_name=strategy_name,
    )

    if not result.get("success"):
        return result

    # Trim trade list for API response — full list stays in CT Backtest Result notes
    result["trades"] = result["trades"][:50]
    return result


@frappe.whitelist()
def get_backtest_results() -> list:
    """Return all CT Backtest Results for the current user, newest first."""
    return frappe.get_all(
        "CT Backtest Result",
        filters={"user": frappe.session.user},
        fields=[
            "name", "strategy_name", "tested_from", "tested_to",
            "total_trades", "win_rate", "profit_factor", "net_profit",
            "max_drawdown", "sharpe_ratio",
        ],
        order_by="creation desc",
        limit=50,
    )

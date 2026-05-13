"""
Risk Engine
===========
Evaluates every validated signal before order placement.
Returns a GO / NO-GO decision with position sizing.

Pipeline position:
  AI Validation Layer (ai_adapter.py)
        ↓
  Risk Engine  ← this module
        ↓
  Trader (trader.py)

Responsibilities:
  1. Position sizing      — risk % of portfolio per trade (Kelly-lite)
  2. Max daily loss guard — halt trading if daily loss exceeds limit
  3. Concurrent position  — max open positions cap
  4. Cooldown             — min gap between trades on same symbol
  5. Trailing stoploss    — update stoploss as price moves in favour
  6. Exposure check       — single-symbol exposure cap
"""

import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime, add_to_date


# ---------------------------------------------------------------------------
# Decision result helpers
# ---------------------------------------------------------------------------

def _go(position_qty: float, position_value: float, reason: str = "") -> dict:
    return {
        "approved":       True,
        "position_qty":   round(position_qty, 8),
        "position_value": round(position_value, 2),
        "reject_reason":  "",
        "approve_reason": reason,
    }


def _no(reason: str) -> dict:
    return {
        "approved":       False,
        "position_qty":   0.0,
        "position_value": 0.0,
        "reject_reason":  reason,
        "approve_reason": "",
    }


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _get_config(user: str) -> dict:
    name = frappe.db.get_value("CT Trading Config", {"user": user, "is_active": 1}, "name")
    if not name:
        frappe.throw(_("No active CT Trading Config found for user {0}").format(user))
    return frappe.get_doc("CT Trading Config", name).as_dict()


# ---------------------------------------------------------------------------
# Daily loss tracker
# ---------------------------------------------------------------------------

def _get_daily_pnl(user: str) -> float:
    today = now_datetime().date().isoformat()
    result = frappe.db.sql(
        """
        SELECT COALESCE(SUM(pnl_inr), 0)
        FROM `tabCT Trade Log`
        WHERE user = %s
          AND DATE(trade_time) = %s
        """,
        (user, today),
    )
    return flt(result[0][0]) if result else 0.0


# ---------------------------------------------------------------------------
# Open position helpers
# ---------------------------------------------------------------------------

def _open_position_count(user: str) -> int:
    return cint(frappe.db.count(
        "CT Open Position",
        filters={"user": user, "status": "Open"},
    ))


def _open_position_for_symbol(user: str, symbol: str) -> dict | None:
    name = frappe.db.get_value(
        "CT Open Position",
        {"user": user, "symbol": symbol, "status": "Open"},
        "name",
    )
    if not name:
        return None
    return frappe.get_doc("CT Open Position", name).as_dict()


# ---------------------------------------------------------------------------
# Cooldown check
# ---------------------------------------------------------------------------

def _cooldown_ok(user: str, symbol: str, cooldown_minutes: int) -> bool:
    if cooldown_minutes <= 0:
        return True
    cutoff = add_to_date(now_datetime(), minutes=-cooldown_minutes)
    recent = frappe.db.count(
        "CT Trade Log",
        filters={
            "user":       user,
            "symbol":     symbol,
            "trade_time": [">", cutoff],
        },
    )
    return cint(recent) == 0


# ---------------------------------------------------------------------------
# Position sizing — Kelly-lite fixed-fractional
# ---------------------------------------------------------------------------

def _calc_position(
    price: float,
    stoploss: float,
    inr_balance: float,
    risk_pct: float,
    max_pos_inr: float,
) -> tuple[float, float]:
    """
    risk_amount  = balance × risk_pct / 100
    qty          = risk_amount / |price - stoploss|
    position_val = qty × price  (hard-capped at max_pos_inr from CT Trading Config.inr_per_trade)
    Returns (qty, position_value_inr).
    """
    if price <= 0 or stoploss <= 0 or inr_balance <= 0:
        return 0.0, 0.0

    risk_per_unit = abs(price - stoploss)
    if risk_per_unit <= 0:
        return 0.0, 0.0

    risk_amount = inr_balance * risk_pct / 100
    raw_qty     = risk_amount / risk_per_unit
    raw_value   = raw_qty * price

    # Cap at inr_per_trade (the configured fixed position size)
    if max_pos_inr > 0 and raw_value > max_pos_inr:
        raw_value = max_pos_inr
        raw_qty   = raw_value / price

    return raw_qty, raw_value


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def evaluate(user: str, signal: dict, inr_balance: float) -> dict:
    """
    Evaluate whether to proceed with a trade signal.

    signal: enriched output from ai_adapter.validate_if_needed()
    inr_balance: current available INR from exchange

    Returns:
    {
      "approved":        bool,
      "position_qty":    float,
      "position_value":  float,
      "reject_reason":   str,
      "approve_reason":  str,
      "stoploss":        float,
      "target":          float,
    }
    """
    cfg = _get_config(user)

    symbol     = signal.get("symbol", "")
    prediction = signal.get("ai_signal") or signal.get("prediction", "HOLD")
    price      = flt(signal.get("close", 0))
    stoploss   = flt(signal.get("ai_stop_loss") or signal.get("suggested_stoploss", 0))
    target     = flt(signal.get("ai_target") or signal.get("suggested_target", 0))
    confidence = flt(signal.get("ai_confidence") or signal.get("confidence_score", 0))

    extra = {"stoploss": stoploss, "target": target}

    if prediction in ("HOLD", "NO TRADE"):
        return {**_no(f"Signal is {prediction}"), **extra}

    # Map actual CT Trading Config field names
    max_open       = cint(cfg.get("max_positions", 5))
    max_daily_loss = flt(cfg.get("max_daily_loss_inr", 0))
    # risk_pct: use stop_loss_pct as the per-trade risk fraction (how much of capital to risk)
    risk_pct       = flt(cfg.get("stop_loss_pct", 1.0))
    cooldown_min   = cint(cfg.get("cooldown_after_loss_min", 30))
    min_confidence = cint(cfg.get("min_confidence_pct", 80))
    # min_trade_inr and max position value both come from inr_per_trade
    min_trade_inr  = flt(cfg.get("inr_per_trade", 500))
    # max_pos_pct is not stored — derive a ceiling from inr_per_trade vs estimated balance
    # Use inr_per_trade as the hard max position value (passed into _calc_position as cap)
    max_pos_inr    = min_trade_inr  # used below to cap qty

    # 1. Confidence gate
    if confidence < min_confidence:
        return {**_no(f"Confidence {confidence:.0f}% < threshold {min_confidence}%"), **extra}

    # 2. Daily loss guard
    if max_daily_loss > 0:
        daily_pnl = _get_daily_pnl(user)
        if daily_pnl <= -max_daily_loss:
            return {**_no(f"Daily loss limit reached: {daily_pnl:.0f} INR"), **extra}

    # 3. Max open positions
    open_count = _open_position_count(user)
    if open_count >= max_open:
        return {**_no(f"Max open positions reached ({open_count}/{max_open})"), **extra}

    # 4. Duplicate position guard
    if _open_position_for_symbol(user, symbol):
        return {**_no(f"Already have an open position for {symbol}"), **extra}

    # 5. Cooldown
    if not _cooldown_ok(user, symbol, cooldown_min):
        return {**_no(f"Cooldown active — {symbol} traded within last {cooldown_min} min"), **extra}

    # 6. Balance check
    if inr_balance < min_trade_inr:
        return {**_no(f"Insufficient balance ({inr_balance:.0f} INR, min {min_trade_inr:.0f})"), **extra}

    # 7. Position sizing
    qty, pos_value = _calc_position(price, stoploss, inr_balance, risk_pct, max_pos_inr)
    if qty <= 0 or pos_value < min_trade_inr:
        return {**_no("Position size too small after risk calculation"), **extra}

    return {**_go(qty, pos_value, f"qty={qty:.6f} value={pos_value:.0f} INR"), **extra}


# ---------------------------------------------------------------------------
# Trailing stoploss updater
# ---------------------------------------------------------------------------

def update_trailing_stoploss(position: dict, current_price: float) -> float:
    """
    Return the updated trailing stoploss for an open position.
    Trails 1.5× ATR behind current price. Only moves in the favourable direction.
    Called by the position monitor every tick.
    """
    direction  = position.get("direction", "BUY")
    current_sl = flt(position.get("stoploss"))
    atr        = flt(position.get("atr_at_entry", 0))

    if not atr or not current_price:
        return current_sl

    trail_distance = atr * 1.5

    if direction == "BUY":
        new_sl = round(current_price - trail_distance, 2)
        return max(new_sl, current_sl)  # only move up
    elif direction == "SELL":
        new_sl = round(current_price + trail_distance, 2)
        return min(new_sl, current_sl) if current_sl > 0 else new_sl

    return current_sl


# ---------------------------------------------------------------------------
# Exit condition checker
# ---------------------------------------------------------------------------

def check_exit(position: dict, current_price: float) -> dict:
    """
    Check if an open position should be closed at current_price.
    Returns {"should_exit": bool, "exit_reason": str}.
    exit_reason: "stoploss" | "target" | ""
    """
    direction = position.get("direction", "BUY")
    stoploss  = flt(position.get("stoploss", 0))
    target    = flt(position.get("target_price", 0))

    if not current_price:
        return {"should_exit": False, "exit_reason": ""}

    if direction == "BUY":
        if stoploss > 0 and current_price <= stoploss:
            return {"should_exit": True, "exit_reason": "stoploss"}
        if target > 0 and current_price >= target:
            return {"should_exit": True, "exit_reason": "target"}
    elif direction == "SELL":
        if stoploss > 0 and current_price >= stoploss:
            return {"should_exit": True, "exit_reason": "stoploss"}
        if target > 0 and current_price <= target:
            return {"should_exit": True, "exit_reason": "target"}

    return {"should_exit": False, "exit_reason": ""}

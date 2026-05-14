"""
Trade Executor
==============
Handles the full order lifecycle: open → monitor → close.

Pipeline position:
  Risk Engine (risk_engine.py)
        ↓
  Trader  ← this module
        ↓
  Exchange (exchange.py)  — live mode only
        ↓
  CT Open Position / CT Trade Log  — always persisted

Modes:
  dry_run = True  (paper trading, default)
    Orders are simulated.  No API calls to CoinDCX.
    Prices filled at close of the candle that triggered the signal.
  dry_run = False (live trading)
    Market orders placed via exchange.place_order().

CT Open Position fields (actual DocType):
  user, symbol, status, buy_price, quantity, entry_time,
  target_price, stop_loss, trailing_stoploss, current_price,
  pnl_inr, pnl_pct, close_reason, close_time, exchange_order_id

CT Trade Log fields (actual DocType):
  user, trade_time, action (BUY/SELL), symbol,
  quantity, price, pnl_inr, pnl_pct, reason, is_paper_trade, position_ref
"""

import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime, get_datetime

from coin_trader.risk_engine import update_trailing_stoploss, check_exit
from coin_trader import notifier


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _get_config(user: str) -> dict:
    name = frappe.db.get_value("CT Trading Config", {"user": user, "is_active": 1}, "name")
    if not name:
        frappe.throw(_("No active CT Trading Config for user {0}").format(user))
    return frappe.get_doc("CT Trading Config", name).as_dict()


def _is_dry_run(cfg: dict) -> bool:
    return bool(cfg.get("dry_run", 1))


# ---------------------------------------------------------------------------
# Open a position
# ---------------------------------------------------------------------------

def open_position(user: str, signal: dict, risk_decision: dict) -> dict:
    """
    Open a new position for the user based on a validated, risk-approved signal.

    signal:        enriched signal from ai_adapter
    risk_decision: output of risk_engine.evaluate()

    Returns the newly created CT Open Position as_dict(), or {} on failure.
    """
    cfg      = _get_config(user)
    dry_run  = _is_dry_run(cfg)
    symbol   = signal.get("symbol", "")
    action   = signal.get("ai_signal") or signal.get("prediction", "BUY")
    price    = flt(signal.get("close", 0))
    stoploss = flt(risk_decision.get("stoploss", 0))
    target   = flt(risk_decision.get("target", 0))
    qty      = flt(risk_decision.get("position_qty", 0))

    if not symbol or not price or qty <= 0:
        frappe.log_error(
            title="Trader: open_position bad params",
            message=f"symbol={symbol} price={price} qty={qty}",
        )
        return {}

    exchange_order_id = ""
    fill_price        = price

    if not dry_run:
        try:
            from coin_trader.exchange import place_order_routed
            side = "buy" if action == "BUY" else "sell"
            resp = place_order_routed(
                user=user, symbol=symbol, side=side,
                order_type="market_order", quantity=qty,
            )
            exchange_order_id = resp.get("id", "")
            fill_price        = flt(resp.get("avg_price") or price)
        except Exception as e:
            frappe.log_error(title=f"Trader: place_order failed ({symbol})", message=str(e))
            return {}

    try:
        doc = frappe.get_doc({
            "doctype":           "CT Open Position",
            "user":              user,
            "symbol":            symbol,
            "status":            "Open",
            "buy_price":         fill_price,
            "quantity":          qty,
            "entry_time":        now_datetime(),
            "target_price":      target,
            "stop_loss":         stoploss,
            "trailing_stoploss": stoploss,
            "current_price":     fill_price,
            "exchange_order_id": exchange_order_id,
        })
        doc.flags.ignore_permissions = True
        doc.insert()
        frappe.db.commit()
        frappe.publish_realtime(
            "ct_position_opened",
            {"symbol": symbol, "direction": action, "qty": qty, "price": fill_price},
            user=user,
        )
        try:
            notifier.notify_position_opened(user, symbol, fill_price, qty, target, stoploss, dry_run)
        except Exception:
            pass
        # Store action in returned dict for downstream use (not a DocType field)
        result = doc.as_dict()
        result["_action"]   = action
        result["_dry_run"]  = dry_run
        return result
    except Exception as e:
        frappe.log_error(title=f"Trader: CT Open Position insert failed ({symbol})", message=str(e))
        return {}


# ---------------------------------------------------------------------------
# Close a position
# ---------------------------------------------------------------------------

def close_position(position: dict, exit_price: float, exit_reason: str) -> bool:
    """
    Close an open position. Updates CT Open Position to Closed and
    inserts a CT Trade Log record.

    position:    CT Open Position as_dict()
    exit_price:  price at which position is closed
    exit_reason: "stoploss" | "target" | "manual" | "trailing_stoploss"

    Returns True on success.
    """
    name     = position.get("name")
    user     = position.get("user")
    symbol   = position.get("symbol")
    # action may come from _action (set by open_position) or default BUY
    action   = position.get("_action", "BUY")
    entry    = flt(position.get("buy_price", 0))
    qty      = flt(position.get("quantity", 0))
    dry_run  = position.get("_dry_run", True)

    if not dry_run:
        try:
            from coin_trader.exchange import place_order_routed
            side = "sell" if action == "BUY" else "buy"
            resp = place_order_routed(
                user=user, symbol=symbol, side=side,
                order_type="market_order", quantity=qty,
            )
            exit_price = flt(resp.get("avg_price") or exit_price)
        except Exception as e:
            frappe.log_error(title=f"Trader: close_order failed ({symbol})", message=str(e))
            return False

    if action == "BUY":
        pnl     = (exit_price - entry) * qty
        pnl_pct = (exit_price - entry) / entry * 100 if entry else 0.0
    else:
        pnl     = (entry - exit_price) * qty
        pnl_pct = (entry - exit_price) / entry * 100 if entry else 0.0

    closed_at = now_datetime()

    try:
        pos_doc = frappe.get_doc("CT Open Position", name)
        pos_doc.status        = "Closed"
        pos_doc.current_price = exit_price
        pos_doc.close_reason  = exit_reason
        pos_doc.close_time    = closed_at
        pos_doc.pnl_inr       = round(pnl, 2)
        pos_doc.pnl_pct       = round(pnl_pct, 4)
        pos_doc.flags.ignore_permissions = True
        pos_doc.save()

        log = frappe.get_doc({
            "doctype":        "CT Trade Log",
            "user":           user,
            "symbol":         symbol,
            "action":         action,
            "trade_time":     closed_at,
            "quantity":       qty,
            "price":          exit_price,
            "pnl_inr":        round(pnl, 2),
            "pnl_pct":        round(pnl_pct, 4),
            "reason":         exit_reason,
            "is_paper_trade": 1 if dry_run else 0,
            "position_ref":   name,
        })
        log.flags.ignore_permissions = True
        log.insert()

        frappe.db.commit()
        frappe.publish_realtime(
            "ct_position_closed",
            {"symbol": symbol, "pnl": pnl, "exit_reason": exit_reason},
            user=user,
        )
        try:
            reason_lower = (exit_reason or "").lower()
            if "stop" in reason_lower or "stoploss" in reason_lower:
                notifier.notify_stoploss_hit(user, symbol, exit_price, pnl, pnl_pct, dry_run)
            elif "target" in reason_lower:
                notifier.notify_target_hit(user, symbol, exit_price, pnl, pnl_pct, dry_run)
            else:
                notifier.notify_position_closed(user, symbol, pnl, pnl_pct, exit_reason, dry_run)
        except Exception:
            pass
        return True

    except Exception as e:
        frappe.log_error(title=f"Trader: close_position failed ({symbol})", message=str(e))
        return False


# ---------------------------------------------------------------------------
# Position monitor — called every minute by scheduler
# ---------------------------------------------------------------------------

def monitor_all_positions() -> None:
    """
    Iterate every open position across all users.
    For each: fetch current price → update trailing stoploss → check exit.
    """
    open_positions = frappe.get_all(
        "CT Open Position",
        filters={"status": "Open"},
        fields=["name", "user", "symbol", "buy_price", "quantity",
                "stop_loss", "trailing_stoploss", "target_price",
                "entry_time", "exchange_order_id"],
    )

    if not open_positions:
        return

    for pos in open_positions:
        try:
            _monitor_single(pos)
        except Exception as e:
            frappe.log_error(
                title=f"Trader: monitor_single failed ({pos.get('symbol')})",
                message=str(e),
            )


def _monitor_single(position: dict) -> None:
    symbol = position.get("symbol", "")

    current_price = _get_current_price(symbol)
    if not current_price:
        return

    # Normalise field names for risk_engine helpers (use stop_loss as stoploss)
    position_normalised = {
        **position,
        "stoploss":    flt(position.get("trailing_stoploss") or position.get("stop_loss")),
        "direction":   "BUY",   # default — no direction field in DocType
        "atr_at_entry": 0,      # ATR not stored — trailing SL won't move without it
    }

    new_sl = update_trailing_stoploss(position_normalised, current_price)
    current_sl = flt(position.get("trailing_stoploss") or position.get("stop_loss"))

    if new_sl != current_sl:
        try:
            frappe.db.set_value("CT Open Position", position["name"], "trailing_stoploss", new_sl)
            position_normalised["stoploss"] = new_sl
        except Exception as e:
            frappe.log_error(title="Trader: trailing SL update failed", message=str(e))

    exit_info = check_exit(position_normalised, current_price)
    if exit_info["should_exit"]:
        close_position(position, current_price, exit_info["exit_reason"])


def _get_current_price(symbol: str) -> float:
    try:
        from coin_trader.market_data import get_market_snapshot
        snap = get_market_snapshot(symbol, "1m")
        return flt(snap.get("close", 0)) if snap else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Full pipeline entry point
# ---------------------------------------------------------------------------

def execute_signal(user: str, signal: dict) -> dict:
    """
    Full pipeline: risk evaluate → open position (if approved).
    """
    from coin_trader.risk_engine import evaluate
    from coin_trader.exchange import get_inr_balance_routed

    try:
        inr_balance = flt(get_inr_balance_routed(user))
    except Exception as e:
        frappe.log_error(title="Trader: get_inr_balance failed", message=str(e))
        inr_balance = 0.0

    risk = evaluate(user, signal, inr_balance)

    if not risk.get("approved"):
        return {
            "executed":      False,
            "position":      {},
            "risk_decision": risk,
            "reject_reason": risk.get("reject_reason", "Risk check failed"),
        }

    position = open_position(user, signal, risk)
    executed = bool(position)

    return {
        "executed":      executed,
        "position":      position,
        "risk_decision": risk,
        "reject_reason": "" if executed else "Position creation failed — see error log",
    }


# ---------------------------------------------------------------------------
# Whitelisted — manual controls from desk
# ---------------------------------------------------------------------------

@frappe.whitelist()
def manual_close_position(position_name: str, reason: str = "manual") -> dict:
    """Close a position manually from the Frappe desk."""
    pos = frappe.get_doc("CT Open Position", position_name)
    if pos.status != "Open":
        return {"success": False, "message": f"Position is already {pos.status}"}
    if pos.user != frappe.session.user and not frappe.has_permission("CT Open Position", "write"):
        frappe.throw(_("Not permitted"))

    price = _get_current_price(pos.symbol)
    if not price:
        return {"success": False, "message": "Could not fetch current price"}

    ok = close_position(pos.as_dict(), price, reason)
    return {"success": ok, "exit_price": price}


@frappe.whitelist()
def get_open_positions() -> list:
    """Return all open positions for the current user."""
    return frappe.get_all(
        "CT Open Position",
        filters={"user": frappe.session.user, "status": "Open"},
        fields=["name", "symbol", "buy_price", "quantity",
                "stop_loss", "trailing_stoploss", "target_price",
                "entry_time", "exchange_order_id", "pnl_inr", "pnl_pct"],
        order_by="entry_time desc",
    )

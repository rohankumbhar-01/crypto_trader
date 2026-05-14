"""
Coin Trader · Outbound Webhook Dispatcher
==========================================
Posts JSON payloads to user-configured URLs when trade events fire.

Public API (called from trader.py, scanner.py, tasks.py):
    fire(user, event, payload)

Events:
    position_opened   — new position entered
    position_closed   — position closed (manual / trailing SL)
    stoploss_hit      — stop-loss triggered
    target_hit        — profit target reached
    scan_complete     — scheduled scan finished with signals
    daily_summary     — end-of-day aggregated P&L report
"""

import frappe
import requests
from frappe.utils import flt, now_datetime


# Map event name → CT Webhook Config field that enables it
_EVENT_FIELD = {
    "position_opened": "on_position_opened",
    "position_closed": "on_position_closed",
    "stoploss_hit":    "on_stoploss_hit",
    "target_hit":      "on_target_hit",
    "scan_complete":   "on_scan_complete",
    "daily_summary":   "on_daily_summary",
}

_TIMEOUT = 8


def _post_outbound(url: str, payload: dict) -> tuple[bool, str]:
    """
    POST payload as JSON to url.
    Returns (success: bool, status_description: str).
    """
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent":   "CoinTrader/1.0",
            },
            timeout=_TIMEOUT,
        )
        return resp.ok, f"HTTP {resp.status_code}"
    except requests.Timeout:
        return False, "Timeout"
    except requests.RequestException as e:
        return False, str(e)[:80]


def fire(user: str, event: str, payload: dict) -> None:
    """
    Look up the user's active CT Webhook Config, check if the event
    is enabled, and POST the enriched payload to the outbound URL.
    Runs in the background — never raises, always logs on failure.
    """
    if event not in _EVENT_FIELD:
        return

    try:
        cfg = frappe.db.get_value(
            "CT Webhook Config",
            {"user": user, "is_active": 1, "outbound_enabled": 1},
            ["name", "outbound_url", _EVENT_FIELD[event]],
            as_dict=True,
        )
        if not cfg or not cfg.get("outbound_url"):
            return
        if not cfg.get(_EVENT_FIELD[event]):
            return  # this event is toggled off

        full_payload = {
            "event":     event,
            "user":      user,
            "timestamp": now_datetime().isoformat(),
            "app":       "Coin Trader",
            **payload,
        }

        ok, status = _post_outbound(cfg["outbound_url"], full_payload)
        status_str = f"{'✅' if ok else '❌'} {event} — {status}"

        frappe.db.set_value("CT Webhook Config", cfg["name"], {
            "last_outbound_at":     now_datetime(),
            "last_outbound_status": status_str[:140],
        })

        if not ok:
            frappe.log_error(
                title=f"Outbound webhook failed: {event}",
                message=f"user={user} url={cfg['outbound_url']} status={status}",
            )

    except Exception as e:
        frappe.log_error(
            title=f"Outbound webhook error: {event}",
            message=str(e),
        )


# ---------------------------------------------------------------------------
# Convenience wrappers — mirror the notifier.py API shape
# ---------------------------------------------------------------------------

def on_position_opened(user, symbol, price, qty, target, stoploss, dry_run=True):
    fire(user, "position_opened", {
        "symbol":    symbol,
        "side":      "BUY",
        "price":     flt(price),
        "quantity":  flt(qty),
        "target":    flt(target),
        "stoploss":  flt(stoploss),
        "dry_run":   dry_run,
        "pnl_inr":   0,
    })


def on_position_closed(user, symbol, pnl, pnl_pct, exit_reason, exit_price=0, dry_run=True):
    fire(user, "position_closed", {
        "symbol":      symbol,
        "exit_price":  flt(exit_price),
        "pnl_inr":     flt(pnl),
        "pnl_pct":     flt(pnl_pct),
        "exit_reason": exit_reason,
        "dry_run":     dry_run,
    })


def on_stoploss_hit(user, symbol, price, pnl, pnl_pct, dry_run=True):
    fire(user, "stoploss_hit", {
        "symbol":   symbol,
        "price":    flt(price),
        "pnl_inr":  flt(pnl),
        "pnl_pct":  flt(pnl_pct),
        "dry_run":  dry_run,
    })


def on_target_hit(user, symbol, price, pnl, pnl_pct, dry_run=True):
    fire(user, "target_hit", {
        "symbol":   symbol,
        "price":    flt(price),
        "pnl_inr":  flt(pnl),
        "pnl_pct":  flt(pnl_pct),
        "dry_run":  dry_run,
    })


def on_scan_complete(user, results: list, symbol_count: int):
    signals = [r for r in results if r.get("prediction") in ("BUY", "SELL")]
    fire(user, "scan_complete", {
        "symbol_count":  symbol_count,
        "signal_count":  len(signals),
        "signals":       signals,
    })


def on_daily_summary(user, summary: dict):
    fire(user, "daily_summary", summary)

"""Authenticated API endpoints for the Coin Trader Chrome extension.

The extension is a control panel only. Exchange credentials stay on the server.
All endpoints are scoped to the authenticated Frappe user. Live trading cannot
be enabled through this module; the scan endpoint refuses non-paper configs.
"""
import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime, getdate


def _current_user():
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Login is required."), frappe.AuthenticationError)
    return user


def _get_user_config(user):
    name = frappe.db.get_value(
        "CT Trading Config",
        {"user": user, "is_active": 1},
        "name",
    )
    if not name:
        return None
    doc = frappe.get_doc("CT Trading Config", name)
    if doc.user != user and "System Manager" not in frappe.get_roles(user):
        frappe.throw(_("You cannot access this trading configuration."), frappe.PermissionError)
    return doc


@frappe.whitelist(methods=["POST"])
def get_dashboard_snapshot():
    """Return the current user's config summary, positions, and recent trade stats."""
    user = _current_user()
    config = _get_user_config(user)
    if not config:
        return {
            "config": None,
            "stats": {"open_positions": 0, "today_trades": 0, "today_pnl_inr": 0, "win_rate_pct": None, "enabled_symbols": 0},
            "recent_trades": [],
            "message": "No active CT Trading Config found for your Frappe user.",
        }

    cfg = {
        "name": config.name,
        "is_active": bool(config.is_active),
        "dry_run": bool(config.dry_run),
        "risk_per_trade_pct": flt(getattr(config, "risk_per_trade_pct", 0)),
        "max_daily_loss_inr": flt(getattr(config, "max_daily_loss_inr", 0)),
    }
    positions = frappe.get_all(
        "CT Open Position",
        filters={"user": user},
        fields=["name", "symbol", "status", "buy_price", "quantity", "current_price", "pnl_inr", "pnl_pct"],
        order_by="creation desc",
        limit_page_length=100,
    )
    open_positions = [p for p in positions if str(p.get("status") or "").lower() not in ("closed", "complete", "completed")]
    trades = frappe.get_all(
        "CT Trade Log",
        filters={"user": user},
        fields=["name", "trade_time", "action", "symbol", "quantity", "price", "pnl_inr", "pnl_pct", "reason", "is_paper_trade"],
        order_by="trade_time desc",
        limit_page_length=100,
    )
    today = getdate(now_datetime())
    today_trades = [t for t in trades if t.get("trade_time") and getdate(t["trade_time"]) == today]
    closed = [t for t in trades if str(t.get("action") or "").upper() == "SELL"]
    wins = sum(1 for t in closed if flt(t.get("pnl_inr")) > 0)
    symbols = frappe.get_all(
        "CT Trading Symbol",
        filters={"parent": config.name, "enabled": 1},
        pluck="symbol",
    )
    return {
        "config": cfg,
        "stats": {
            "open_positions": len(open_positions),
            "today_trades": len(today_trades),
            "today_pnl_inr": round(sum(flt(t.get("pnl_inr")) for t in today_trades), 2),
            "win_rate_pct": round(wins * 100 / len(closed), 1) if closed else None,
            "enabled_symbols": len(symbols),
        },
        "positions": open_positions[:10],
        "recent_trades": trades[:5],
        "message": "Paper trading is active." if config.dry_run else "LIVE TRADING IS ENABLED in the server configuration.",
    }


@frappe.whitelist(methods=["POST"])
def force_paper_mode():
    """Switch the authenticated user's active config to paper mode only."""
    user = _current_user()
    config = _get_user_config(user)
    if not config:
        frappe.throw(_("No active trading configuration found."))
    if not config.dry_run:
        config.dry_run = 1
        config.save()
        frappe.db.commit()
    return {"success": True, "message": "Paper mode is enabled for your active configuration."}


@frappe.whitelist(methods=["POST"])
def run_paper_scan():
    """Run a single scanner pass only when the user's active config is in paper mode."""
    user = _current_user()
    config = _get_user_config(user)
    if not config:
        frappe.throw(_("No active trading configuration found."))
    config.reload()
    if not config.is_active or not config.dry_run:
        frappe.throw(_("Paper scan refused: activate an active paper-trading configuration first."))
    from coin_trader.scanner import _scan_user
    _scan_user({
        "name": config.name,
        "user": user,
        "candle_interval": getattr(config, "candle_interval", None),
        "min_confidence_pct": cint(getattr(config, "min_confidence_pct", 80)),
    })
    return {"success": True, "message": "Paper scan completed. Refresh to view the latest results."}
"""
Context loader for /ct-dashboard
"""

import frappe
from frappe.utils import flt, cint, now_datetime


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/ct-dashboard"
        raise frappe.Redirect

    user = frappe.session.user
    context.no_cache = 1
    context.title = "Coin Trader Dashboard"

    # Safe defaults — guaranteed to be set before any DB call
    context.config               = {}
    context.is_active            = False
    context.dry_run              = True
    context.interval             = "1h"
    context.min_conf             = 80
    context.symbols              = []
    context.open_positions_count = 0
    context.today_pnl            = 0.0
    context.model_trained        = False

    try:
        cfg_name = frappe.db.get_value(
            "CT Trading Config",
            {"user": user, "is_active": 1},
            "name",
        )
        if cfg_name:
            cfg = frappe.get_doc("CT Trading Config", cfg_name).as_dict()
            context.config    = cfg
            context.is_active = True
            context.dry_run   = bool(cfg.get("dry_run", 1))
            context.interval  = cfg.get("candle_interval") or "1h"
            context.min_conf  = cint(cfg.get("min_confidence_pct", 80))
            context.symbols   = frappe.get_all(
                "CT Trading Symbol",
                filters={"parent": cfg_name, "enabled": 1},
                fields=["symbol"],
                pluck="symbol",
            )
    except Exception as e:
        frappe.log_error(title="ct-dashboard: config load failed", message=str(e))

    try:
        context.open_positions_count = cint(frappe.db.count(
            "CT Open Position",
            filters={"user": user, "status": "Open"},
        ))
    except Exception as e:
        frappe.log_error(title="ct-dashboard: position count failed", message=str(e))

    try:
        today = now_datetime().date().isoformat()
        pnl_row = frappe.db.sql(
            "SELECT COALESCE(SUM(pnl_inr),0) FROM `tabCT Trade Log` "
            "WHERE user=%s AND DATE(trade_time)=%s",
            (user, today),
        )
        context.today_pnl = flt(pnl_row[0][0]) if pnl_row else 0.0
    except Exception as e:
        frappe.log_error(title="ct-dashboard: pnl load failed", message=str(e))

    try:
        from coin_trader.ml.train_model import model_exists
        context.model_trained = model_exists(user)
    except Exception as e:
        frappe.log_error(title="ct-dashboard: model_exists failed", message=str(e))

"""
Scanner
=======
Orchestrates the full trading pipeline on a scheduled cadence.

Pipeline per symbol per user:
  1. Probability Engine   — ML predict + composite score
  2. AI Validation        — validate_if_needed() (skipped if call_ai=False)
  3. Risk Engine          — evaluate() — position sizing + guards
  4. Trader               — execute_signal() — open position (paper or live)

Scheduler hooks (hooks.py):
  "*/15 * * * *"  → coin_trader.tasks.run_scanner     (every 15 min)
  "daily"         → coin_trader.tasks.generate_daily_summary

Scan results are published to the desk via Frappe realtime so the
dashboard can update without polling.
"""

import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime
from coin_trader import notifier, outbound_webhook


# ---------------------------------------------------------------------------
# Main scanner entry point
# ---------------------------------------------------------------------------

def run_scanner() -> None:
    """
    Called every 15 minutes by the Frappe scheduler.
    Iterates every active CT Trading Config and scans all enabled symbols.
    Each user-symbol pair runs the full ML→AI→Risk→Trade pipeline.
    """
    configs = frappe.get_all(
        "CT Trading Config",
        filters={"is_active": 1},
        fields=["name", "user", "candle_interval", "min_confidence_pct"],
    )

    for cfg in configs:
        user = cfg["user"]
        try:
            _scan_user(cfg)
        except Exception as e:
            frappe.log_error(
                title=f"Scanner: user scan failed ({user})",
                message=str(e),
            )


def _scan_user(cfg: dict) -> None:
    """Run the scan pipeline for a single user across all their enabled symbols."""
    from coin_trader.ml.predict import model_exists

    user     = cfg["user"]
    interval = cfg.get("candle_interval") or "1h"
    min_conf = cint(cfg.get("min_confidence_pct") or 80)

    if not model_exists(user):
        return  # model not yet trained — skip silently

    symbols = frappe.get_all(
        "CT Trading Symbol",
        filters={"parent": cfg["name"], "enabled": 1},
        fields=["symbol"],
        pluck="symbol",
    )
    if not symbols:
        return

    results = []
    for symbol in symbols:
        try:
            result = _scan_symbol(user, symbol, interval, min_conf)
            if result:
                results.append(result)
        except Exception as e:
            frappe.log_error(
                title=f"Scanner: symbol scan failed ({user}/{symbol})",
                message=str(e),
            )

    if results:
        frappe.publish_realtime(
            "ct_scan_complete",
            {
                "user":         user,
                "scanned_at":   str(now_datetime()),
                "symbol_count": len(symbols),
                "signals":      results,
            },
            user=user,
        )
        try:
            notifier.notify_scan_summary(user, results, len(symbols))
        except Exception:
            pass
        try:
            outbound_webhook.on_scan_complete(user, results, len(symbols))
        except Exception:
            pass


def _scan_symbol(
    user: str,
    symbol: str,
    interval: str,
    min_confidence: int,
) -> dict | None:
    """
    Run the full pipeline for one symbol.
    Returns a summary dict, or None if no actionable signal.
    """
    from coin_trader.probability_engine import run_probability_engine
    from coin_trader.ai_adapter import validate_if_needed
    from coin_trader.trader import execute_signal

    # 1. Probability Engine
    signal = run_probability_engine(user, symbol, interval, min_confidence=min_confidence)
    if not signal:
        return None

    # 2. AI Validation (only if call_ai=True)
    signal = validate_if_needed(user, signal)

    # 3. Risk + Trade execution
    outcome = execute_signal(user, signal)

    return {
        "symbol":         symbol,
        "prediction":     signal.get("ai_signal") or signal.get("prediction"),
        "confidence":     signal.get("ai_confidence") or signal.get("confidence_score"),
        "signal_quality": signal.get("signal_quality"),
        "call_ai":        signal.get("call_ai"),
        "ai_validated":   signal.get("ai_validated"),
        "executed":       outcome.get("executed"),
        "reject_reason":  outcome.get("reject_reason"),
    }


# ---------------------------------------------------------------------------
# Daily summary generator
# ---------------------------------------------------------------------------

def generate_daily_summary() -> None:
    """
    Called once daily by the Frappe scheduler.
    Aggregates CT Trade Log records for yesterday into CT Daily Summary
    for each active user.
    """
    users = frappe.get_all(
        "CT Trading Config",
        filters={"is_active": 1},
        fields=["user"],
        pluck="user",
    )
    for user in users:
        try:
            _generate_user_summary(user)
        except Exception as e:
            frappe.log_error(
                title=f"Scanner: daily summary failed ({user})",
                message=str(e),
            )


def _generate_user_summary(user: str) -> None:
    """Build and save CT Daily Summary for one user for yesterday's trades."""
    import datetime
    yesterday = (now_datetime().date() - datetime.timedelta(days=1)).isoformat()

    rows = frappe.db.sql(
        """
        SELECT
            COUNT(*)                                             AS total_trades,
            SUM(CASE WHEN pnl_inr > 0 THEN 1 ELSE 0 END)       AS winning_trades,
            SUM(CASE WHEN pnl_inr < 0 THEN 1 ELSE 0 END)       AS losing_trades,
            COALESCE(SUM(pnl_inr), 0)                           AS total_pnl_inr,
            COALESCE(MAX(pnl_inr), 0)                           AS best_trade_inr,
            COALESCE(MIN(pnl_inr), 0)                           AS worst_trade_inr,
            COALESCE(AVG(pnl_inr), 0)                           AS avg_pnl_inr,
            SUM(CASE WHEN is_paper_trade = 1 THEN 1 ELSE 0 END) AS paper_trades,
            SUM(CASE WHEN is_paper_trade = 0 THEN 1 ELSE 0 END) AS live_trades
        FROM `tabCT Trade Log`
        WHERE user = %s
          AND DATE(trade_time) = %s
        """,
        (user, yesterday),
        as_dict=True,
    )

    if not rows or not cint(rows[0].get("total_trades")):
        return  # no trades yesterday — skip

    row    = rows[0]
    total  = cint(row["total_trades"])
    wins   = cint(row["winning_trades"])
    win_rate = round(wins / total * 100, 2) if total else 0.0

    # Profit factor = gross_profit / |gross_loss|
    pf_rows = frappe.db.sql(
        """
        SELECT
            COALESCE(SUM(CASE WHEN pnl_inr > 0 THEN pnl_inr ELSE 0 END), 0) AS gross_profit,
            COALESCE(SUM(CASE WHEN pnl_inr < 0 THEN pnl_inr ELSE 0 END), 0) AS gross_loss
        FROM `tabCT Trade Log`
        WHERE user = %s
          AND DATE(trade_time) = %s
        """,
        (user, yesterday),
        as_dict=True,
    )
    gross_profit  = flt(pf_rows[0]["gross_profit"]) if pf_rows else 0.0
    gross_loss    = abs(flt(pf_rows[0]["gross_loss"])) if pf_rows else 0.0
    profit_factor = round(gross_profit / gross_loss, 3) if gross_loss > 0 else 0.0

    # Avoid duplicate for the same date
    if frappe.db.get_value("CT Daily Summary", {"user": user, "summary_date": yesterday}, "name"):
        return

    doc = frappe.get_doc({
        "doctype":         "CT Daily Summary",
        "user":            user,
        "summary_date":    yesterday,
        "total_trades":    total,
        "winning_trades":  wins,
        "losing_trades":   cint(row["losing_trades"]),
        "win_rate_pct":    win_rate,
        "total_pnl_inr":   round(flt(row["total_pnl_inr"]), 2),
        "best_trade_inr":  round(flt(row["best_trade_inr"]), 2),
        "worst_trade_inr": round(flt(row["worst_trade_inr"]), 2),
        "avg_pnl_inr":     round(flt(row["avg_pnl_inr"]), 2),
        "profit_factor":   profit_factor,
        "paper_trades":    cint(row["paper_trades"]),
        "live_trades":     cint(row["live_trades"]),
    })
    doc.flags.ignore_permissions = True
    doc.insert()
    frappe.db.commit()


# ---------------------------------------------------------------------------
# Whitelisted — manual scan trigger from desk
# ---------------------------------------------------------------------------

@frappe.whitelist()
def run_scanner_api() -> dict:
    """
    Manually trigger a full scan from the Frappe desk.
    Returns scan results for the current user only.
    """
    from coin_trader.ml.predict import model_exists

    user = frappe.session.user

    if not model_exists(user):
        return {"error": "No trained model found. Please wait for daily training."}

    cfg_name = frappe.db.get_value(
        "CT Trading Config",
        {"user": user, "is_active": 1},
        "name",
    )
    if not cfg_name:
        return {"error": "No active CT Trading Config found."}

    cfg = frappe.get_doc("CT Trading Config", cfg_name).as_dict()
    cfg["name"] = cfg_name

    interval = cfg.get("candle_interval") or "1h"
    min_conf = cint(cfg.get("min_confidence_pct") or 80)

    symbols = frappe.get_all(
        "CT Trading Symbol",
        filters={"parent": cfg_name, "enabled": 1},
        fields=["symbol"],
        pluck="symbol",
    )

    results = []
    for symbol in symbols:
        try:
            result = _scan_symbol(user, symbol, interval, min_conf)
            if result:
                results.append(result)
        except Exception as e:
            results.append({"symbol": symbol, "error": str(e)})

    return {
        "scanned_at":   str(now_datetime()),
        "symbol_count": len(symbols),
        "signals":      results,
    }

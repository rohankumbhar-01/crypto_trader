import frappe


def run_scanner():
    """Runs every 15 minutes — ML→AI→Risk→Trade pipeline for all active users."""
    if not frappe.db:
        return
    from coin_trader.scanner import run_scanner as _run
    _run()


def monitor_positions():
    """Runs every scheduler tick (all) — 1-min position monitoring."""
    if not frappe.db:
        return
    from coin_trader.trader import monitor_all_positions
    monitor_all_positions()


def daily_ml_retrain():
    """Runs at 2 AM daily — retrains LightGBM models for all active users."""
    from coin_trader.ml.train_model import daily_train
    from coin_trader.ml.predict import invalidate_model_cache

    daily_train()

    configs = frappe.get_all("CT Trading Config", filters={"is_active": 1}, fields=["user"])
    for cfg in configs:
        invalidate_model_cache(cfg["user"])


def generate_daily_summary():
    """Runs daily — aggregates trade log into CT Daily Summary and sends notifications."""
    from coin_trader.scanner import generate_daily_summary as _summarise
    from coin_trader.notifier import notify_all_daily_summary
    _summarise()
    try:
        notify_all_daily_summary()
    except Exception:
        pass
    # Outbound webhook — daily summary per active user
    try:
        from coin_trader.outbound_webhook import on_daily_summary
        import datetime
        yesterday = (frappe.utils.now_datetime().date() - datetime.timedelta(days=1)).isoformat()
        users = frappe.get_all("CT Trading Config", filters={"is_active": 1}, fields=["user"], pluck="user")
        for user in users:
            try:
                row = frappe.db.sql(
                    "SELECT COALESCE(SUM(pnl_inr),0), COUNT(*), "
                    "SUM(CASE WHEN pnl_inr>0 THEN 1 ELSE 0 END) "
                    "FROM `tabCT Trade Log` WHERE user=%s AND DATE(trade_time)=%s",
                    (user, yesterday),
                )
                if row:
                    on_daily_summary(user, {
                        "date":          yesterday,
                        "total_pnl_inr": float(row[0][0] or 0),
                        "trade_count":   int(row[0][1] or 0),
                        "winning_trades": int(row[0][2] or 0),
                    })
            except Exception:
                pass
    except Exception:
        pass

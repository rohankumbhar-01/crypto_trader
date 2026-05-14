"""
Coin Trader · Notification System
==================================
Sends trade/signal/daily-summary alerts via Telegram and/or Email.

Public API (call from trader.py, scanner.py, tasks.py):
    notify_position_opened(user, symbol, price, qty, target, stoploss, dry_run)
    notify_position_closed(user, symbol, pnl, pnl_pct, exit_reason, dry_run)
    notify_stoploss_hit(user, symbol, price, pnl, pnl_pct)
    notify_target_hit(user, symbol, price, pnl, pnl_pct)
    notify_scan_summary(user, results, symbol_count)
    notify_daily_summary(user)
"""

import frappe
import requests
from frappe.utils import flt, now_datetime, get_datetime


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_config(user: str):
    """Return active CT Notification Config for user, or None."""
    name = frappe.db.get_value(
        "CT Notification Config",
        {"user": user, "is_active": 1},
        "name",
    )
    if not name:
        return None
    return frappe.get_doc("CT Notification Config", name)


def _fmt_inr(n) -> str:
    v = flt(n)
    if abs(v) >= 100000:
        return f"₹{v/100000:.2f}L"
    return f"₹{v:,.2f}"


def _sign(n) -> str:
    return "+" if flt(n) >= 0 else ""


def _mode_tag(dry_run) -> str:
    return "📄 PAPER" if dry_run else "⚡ LIVE"


# ---------------------------------------------------------------------------
# Telegram sender
# ---------------------------------------------------------------------------

def _esc(s) -> str:
    """Escape special HTML chars in dynamic values sent to Telegram."""
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _send_telegram(bot_token: str, chat_id: str, text: str) -> bool:
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
        if not resp.ok:
            # If HTML parse fails, retry with plain text (strip tags)
            import re
            plain = re.sub(r"<[^>]+>", "", text)
            resp2 = requests.post(
                url,
                json={"chat_id": chat_id, "text": plain},
                timeout=8,
            )
            if not resp2.ok:
                frappe.log_error(
                    title="CT Notifier: Telegram failed",
                    message=f"Status {resp.status_code}: {resp.text[:300]}",
                )
                return False
        return True
    except Exception as e:
        frappe.log_error(title="CT Notifier: Telegram error", message=str(e))
        return False


# ---------------------------------------------------------------------------
# Email sender
# ---------------------------------------------------------------------------

def _send_email(to_email: str, subject: str, body: str) -> bool:
    try:
        frappe.sendmail(
            recipients=[to_email],
            subject=subject,
            message=body,
            now=True,
        )
        return True
    except Exception as e:
        frappe.log_error(title="CT Notifier: Email error", message=str(e))
        return False


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def _dispatch(user: str, subject: str, telegram_text: str, email_html: str = None):
    """Send notification via configured channels for the user."""
    cfg = _get_config(user)
    if not cfg:
        return

    channel  = cfg.channel or "Telegram"
    email_html = email_html or telegram_text.replace("<b>", "<strong>").replace("</b>", "</strong>")

    if channel in ("Telegram", "Both"):
        if cfg.bot_token and cfg.chat_id:
            _send_telegram(cfg.get_password("bot_token"), cfg.chat_id, telegram_text)

    if channel in ("Email", "Both"):
        if cfg.email:
            _send_email(
                cfg.email,
                subject,
                f"<pre style='font-family:monospace;font-size:14px'>{email_html}</pre>",
            )


# ---------------------------------------------------------------------------
# Public notification functions
# ---------------------------------------------------------------------------

def notify_position_opened(user: str, symbol: str, price: float, qty: float,
                            target: float, stoploss: float, dry_run: bool = True):
    subject = f"🟢 BUY {symbol} @ {_fmt_inr(price)}"
    text = (
        f"🟢 <b>POSITION OPENED</b>  {_mode_tag(dry_run)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Symbol   :</b> {_esc(symbol)}\n"
        f"<b>Buy @    :</b> {_esc(_fmt_inr(price))}\n"
        f"<b>Qty      :</b> {qty:.6f}\n"
        f"<b>Value    :</b> {_esc(_fmt_inr(price * qty))}\n"
        f"<b>Target   :</b> {_esc(_fmt_inr(target))}\n"
        f"<b>Stop-Loss:</b> {_esc(_fmt_inr(stoploss))}\n"
        f"<b>Time     :</b> {now_datetime().strftime('%d %b %Y %H:%M:%S')}"
    )
    _dispatch(user, subject, text)


def notify_position_closed(user: str, symbol: str, pnl: float, pnl_pct: float,
                            exit_reason: str, dry_run: bool = True):
    emoji  = "✅" if flt(pnl) >= 0 else "🔴"
    label  = "PROFIT" if flt(pnl) >= 0 else "LOSS"
    subject = f"{emoji} CLOSED {symbol} · {_sign(pnl)}{_fmt_inr(pnl)}"
    text = (
        f"{emoji} <b>POSITION CLOSED</b>  {_mode_tag(dry_run)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Symbol :</b> {_esc(symbol)}\n"
        f"<b>P&amp;L    :</b> {_sign(pnl)}{_esc(_fmt_inr(pnl))}  ({_sign(pnl_pct)}{flt(pnl_pct):.2f}%)  [{label}]\n"
        f"<b>Reason :</b> {_esc(exit_reason)}\n"
        f"<b>Time   :</b> {now_datetime().strftime('%d %b %Y %H:%M:%S')}"
    )
    _dispatch(user, subject, text)


def notify_stoploss_hit(user: str, symbol: str, price: float,
                         pnl: float, pnl_pct: float, dry_run: bool = True):
    subject = f"🛑 STOP-LOSS {symbol} · {_fmt_inr(price)}"
    text = (
        f"🛑 <b>STOP-LOSS HIT</b>  {_mode_tag(dry_run)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Symbol :</b> {_esc(symbol)}\n"
        f"<b>Exit @ :</b> {_esc(_fmt_inr(price))}\n"
        f"<b>Loss   :</b> {_esc(_fmt_inr(pnl))}  ({flt(pnl_pct):.2f}%)\n"
        f"<b>Time   :</b> {now_datetime().strftime('%d %b %Y %H:%M:%S')}"
    )
    _dispatch(user, subject, text)


def notify_target_hit(user: str, symbol: str, price: float,
                       pnl: float, pnl_pct: float, dry_run: bool = True):
    subject = f"🎯 TARGET HIT {symbol} · {_fmt_inr(price)}"
    text = (
        f"🎯 <b>TARGET REACHED</b>  {_mode_tag(dry_run)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Symbol :</b> {_esc(symbol)}\n"
        f"<b>Exit @ :</b> {_esc(_fmt_inr(price))}\n"
        f"<b>Profit :</b> +{_esc(_fmt_inr(pnl))}  (+{flt(pnl_pct):.2f}%)\n"
        f"<b>Time   :</b> {now_datetime().strftime('%d %b %Y %H:%M:%S')}"
    )
    _dispatch(user, subject, text)


def notify_scan_summary(user: str, results: list, symbol_count: int = 0):
    if not results:
        return
    buys  = [r for r in results if (r.get("ai_signal") or r.get("prediction", "")) == "BUY"]
    sells = [r for r in results if (r.get("ai_signal") or r.get("prediction", "")) == "SELL"]
    if not buys and not sells:
        return  # Only notify if there are actionable signals

    subject = f"🔎 Scan: {len(buys)} BUY · {len(sells)} SELL signals"
    lines = [
        "🔎 <b>SCAN COMPLETE</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<b>Scanned :</b> {symbol_count or len(results)} symbols",
        f"<b>BUY     :</b> {len(buys)}  |  <b>SELL:</b> {len(sells)}",
        "",
    ]
    for r in buys[:5]:
        conf   = flt(r.get("ai_confidence") or r.get("confidence_score", 0))
        reason = _esc((r.get("approve_reason") or r.get("reason") or "")[:60])
        lines.append(f"🟢 <b>{_esc(r.get('symbol','?'))}</b>  {conf:.0f}%  {reason}")
    for r in sells[:3]:
        conf   = flt(r.get("ai_confidence") or r.get("confidence_score", 0))
        reason = _esc((r.get("reject_reason") or r.get("reason") or "")[:60])
        lines.append(f"🔴 <b>{_esc(r.get('symbol','?'))}</b>  {conf:.0f}%  {reason}")
    lines.append(f"\n<b>Time:</b> {now_datetime().strftime('%d %b %Y %H:%M:%S')}")
    _dispatch(user, subject, "\n".join(lines))


def notify_daily_summary(user: str):
    """Send end-of-day P&L summary. Called by scheduler."""
    today = now_datetime().date().isoformat()
    try:
        row = frappe.db.sql(
            """
            SELECT COALESCE(SUM(pnl_inr),0) as pnl,
                   COUNT(*) as trades,
                   SUM(CASE WHEN pnl_inr > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN pnl_inr < 0 THEN 1 ELSE 0 END) as losses
            FROM `tabCT Trade Log`
            WHERE user=%s AND DATE(trade_time)=%s
            """,
            (user, today),
            as_dict=True,
        )
        if not row:
            return
        r = row[0]
        pnl    = flt(r.pnl)
        trades = int(r.trades or 0)
        wins   = int(r.wins or 0)
        losses = int(r.losses or 0)
        win_rate = (wins / trades * 100) if trades else 0
        emoji  = "📈" if pnl >= 0 else "📉"

        open_count = frappe.db.count("CT Open Position", {"user": user, "status": "Open"})

        subject = f"{emoji} Daily P&L: {_sign(pnl)}{_fmt_inr(pnl)} · {trades} trades"
        text = (
            f"{emoji} <b>DAILY SUMMARY</b>  —  {today}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Total P&L  :</b> {_sign(pnl)}{_fmt_inr(pnl)}\n"
            f"<b>Trades     :</b> {trades}  (W: {wins}  L: {losses})\n"
            f"<b>Win Rate   :</b> {win_rate:.1f}%\n"
            f"<b>Open Pos.  :</b> {open_count}\n"
            f"<b>Date       :</b> {today}"
        )
        _dispatch(user, subject, text)
    except Exception as e:
        frappe.log_error(title="CT Notifier: daily summary failed", message=str(e))


def notify_all_daily_summary():
    """Notify every user with an active config. Called by scheduler."""
    users = frappe.get_all(
        "CT Notification Config",
        filters={"is_active": 1},
        pluck="user",
    )
    for user in users:
        try:
            notify_daily_summary(user)
        except Exception as e:
            frappe.log_error(title=f"CT Notifier: daily summary for {user}", message=str(e))

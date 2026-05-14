"""
Coin Trader · Inbound Webhook Handler
======================================
Receives external trade signals via HTTP POST and feeds them into
the full ML→Risk→Trade pipeline immediately.

Endpoint (public, no Frappe login required):
  POST /api/method/coin_trader.webhook_handler.receive

Request headers:
  X-CT-Token: <secret_token from CT Webhook Config>
  Content-Type: application/json

Request body:
  {
    "user":       "user@example.com",   # which CT Trading Config to use
    "symbol":     "BTCINR",             # trading symbol
    "signal":     "BUY",                # BUY | SELL | HOLD
    "confidence": 85,                   # optional 0-100, default 80
    "price":      77500.0,              # optional override (uses live price if omitted)
    "source":     "TradingView"         # optional label for logs
  }

Response:
  {
    "status":    "executed" | "skipped" | "rejected" | "error",
    "message":   "human readable result",
    "position":  {...}  # only when executed
  }
"""

import frappe
from frappe import _
from frappe.utils import flt, cint, now_datetime


def _validate_token(user: str, token: str) -> bool:
    """Check that the provided token matches the user's CT Webhook Config secret."""
    cfg_name = frappe.db.get_value(
        "CT Webhook Config",
        {"user": user, "is_active": 1, "inbound_enabled": 1},
        "name",
    )
    if not cfg_name:
        return False
    stored = frappe.utils.password.get_decrypted_password(
        "CT Webhook Config", cfg_name, "secret_token"
    )
    return stored and token and stored == token


def _get_live_price(symbol: str) -> float:
    """Fetch current price via the active exchange adapter."""
    try:
        from coin_trader.exchange import get_active_exchange
        from coin_trader.dashboard_api import _fetch_ticker_cached, _norm_market_key
        user_guess = frappe.db.get_value("CT Trading Config", {"is_active": 1}, "user") or "Administrator"
        exch = get_active_exchange(user_guess)

        if exch == "Binance":
            from coin_trader.exchange_binance import get_price, _get_usdt_inr_rate
            s = symbol.upper()
            bnb_sym = s[:-3] + "USDT" if s.endswith("INR") else s
            usdt_price = get_price(bnb_sym)
            return round(usdt_price * _get_usdt_inr_rate(), 4) if s.endswith("INR") else usdt_price

        if exch == "WazirX":
            from coin_trader.exchange_wazirx import get_price as wzx_price
            return wzx_price(symbol)

        # CoinDCX — use ticker cache
        ticker = _fetch_ticker_cached() or []
        key = _norm_market_key(symbol)
        for t in ticker:
            if t.get("market") == key:
                return flt(t.get("last_price", 0))
    except Exception as e:
        frappe.log_error(title="Webhook: get_live_price failed", message=str(e))
    return 0.0


@frappe.whitelist(allow_guest=True)
def receive(**kwargs):
    """
    Main inbound webhook endpoint.
    Accepts POST from TradingView, bots, or any HTTP client.
    """
    import json

    # Parse body — frappe passes form fields as kwargs; JSON body via request
    try:
        raw = frappe.request.get_data(as_text=True)
        if raw:
            data = json.loads(raw)
        else:
            data = kwargs
    except Exception:
        data = kwargs

    # --- Extract fields ---
    user       = str(data.get("user") or "").strip()
    symbol     = str(data.get("symbol") or "").strip().upper()
    signal     = str(data.get("signal") or "").strip().upper()
    confidence = flt(data.get("confidence", 80))
    price      = flt(data.get("price", 0))
    source     = str(data.get("source") or "webhook")[:50]

    # --- Token from header or body ---
    token = (
        frappe.request.headers.get("X-CT-Token")
        or str(data.get("token") or "")
    ).strip()

    # --- Basic validation ---
    if not user:
        return {"status": "error", "message": "user is required"}
    if not symbol:
        return {"status": "error", "message": "symbol is required"}
    if signal not in ("BUY", "SELL", "HOLD"):
        return {"status": "error", "message": f"signal must be BUY, SELL, or HOLD — got '{signal}'"}

    # --- Authenticate ---
    if not _validate_token(user, token):
        frappe.log_error(
            title="Webhook: invalid token",
            message=f"user={user} symbol={symbol} source={source} token={token[:8]}...",
        )
        return {"status": "error", "message": "Invalid or missing secret token"}

    # HOLD signals — acknowledge but don't trade
    if signal == "HOLD":
        _stamp_inbound(user)
        return {"status": "skipped", "message": "HOLD signal acknowledged — no action taken"}

    # --- Check active config ---
    cfg_name = frappe.db.get_value("CT Trading Config", {"user": user, "is_active": 1}, "name")
    if not cfg_name:
        return {"status": "error", "message": f"No active CT Trading Config for user {user}"}

    # --- Get live price if not provided ---
    if not price:
        price = _get_live_price(symbol)
    if not price:
        return {"status": "error", "message": f"Could not determine price for {symbol}"}

    # --- Build signal dict compatible with execute_signal() ---
    signal_dict = {
        "symbol":           symbol,
        "prediction":       signal,
        "ai_signal":        signal,
        "confidence_score": confidence,
        "ai_confidence":    confidence,
        "close":            price,
        "signal_quality":   "webhook",
        "call_ai":          False,
        "ai_validated":     True,   # trust the external source
        "source":           source,
    }

    # --- Execute through the full risk + trade pipeline ---
    try:
        frappe.set_user(user)
        from coin_trader.trader import execute_signal
        outcome = execute_signal(user, signal_dict)
        _stamp_inbound(user)

        frappe.log_error(
            title=f"Webhook: signal processed ({symbol})",
            message=f"source={source} signal={signal} confidence={confidence}% executed={outcome.get('executed')}",
        )

        if outcome.get("executed"):
            return {
                "status":   "executed",
                "message":  f"{signal} order placed for {symbol} at ₹{price:,.2f}",
                "position": {k: v for k, v in (outcome.get("position") or {}).items() if not k.startswith("_")},
            }
        else:
            return {
                "status":  "rejected",
                "message": outcome.get("reject_reason") or "Risk check failed",
            }

    except Exception as e:
        frappe.log_error(title=f"Webhook: execute_signal error ({symbol})", message=str(e))
        return {"status": "error", "message": str(e)[:200]}


def _stamp_inbound(user: str):
    """Update last_inbound_at on the webhook config."""
    try:
        cfg_name = frappe.db.get_value(
            "CT Webhook Config",
            {"user": user, "is_active": 1},
            "name",
        )
        if cfg_name:
            frappe.db.set_value(
                "CT Webhook Config", cfg_name,
                "last_inbound_at", now_datetime(),
            )
    except Exception:
        pass

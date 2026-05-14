"""
Coin Trader · Binance Exchange Adapter
=======================================
Implements the same interface as exchange.py (CoinDCX) but for Binance.

Binance API docs: https://binance-docs.github.io/apidocs/spot/en/

Key differences from CoinDCX:
- Symbol format: BTCUSDT (no INR pairs — Binance is USDT-based)
- Signature: HMAC-SHA256 over query string (not JSON body)
- Balance returned in USDT / BTC / coin units
- INR equivalent computed via USDT/INR rate from CoinDCX ticker
"""

import hashlib
import hmac
import time

import frappe
import requests
from frappe import _
from frappe.utils import flt

_REST_BASE   = "https://api.binance.com"
_TIMEOUT     = 10


# ---------------------------------------------------------------------------
# Credential helper
# ---------------------------------------------------------------------------

def _get_binance_credential(user: str) -> dict:
    name = frappe.db.get_value(
        "CT Exchange Credential",
        {"user": user, "is_active": 1, "exchange": "Binance"},
        ["name", "api_key", "api_secret"],
        as_dict=True,
    )
    if not name:
        frappe.throw(_("No active Binance credential found for user {0}.").format(user))
    from frappe.utils.password import get_decrypted_password
    name["api_secret"] = get_decrypted_password(
        "CT Exchange Credential", name["name"], "api_secret"
    )
    return name


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------

def _sign_query(params: dict, api_secret: str) -> str:
    """Binance uses HMAC-SHA256 over the URL-encoded query string."""
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(
        api_secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _get(path: str, params: dict = None, api_key: str = None, api_secret: str = None) -> dict | list:
    """Authenticated or public GET request to Binance REST API."""
    params = params or {}
    headers = {}
    if api_key:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = _sign_query(params, api_secret)
        headers["X-MBX-APIKEY"] = api_key
    try:
        resp = requests.get(
            f"{_REST_BASE}{path}", params=params, headers=headers, timeout=_TIMEOUT
        )
        if not resp.ok:
            frappe.log_error(
                title="Binance API Error",
                message=f"{resp.status_code}: {resp.text[:300]} — {path}",
            )
            frappe.throw(_("Binance API error ({0}): {1}").format(resp.status_code, resp.text[:200]))
        return resp.json()
    except requests.RequestException as e:
        frappe.log_error(title="Binance Network Error", message=str(e))
        frappe.throw(_("Binance network error: {0}").format(str(e)))


def _post(path: str, params: dict, api_key: str, api_secret: str) -> dict:
    """Authenticated POST to Binance REST API."""
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign_query(params, api_secret)
    try:
        resp = requests.post(
            f"{_REST_BASE}{path}",
            params=params,
            headers={"X-MBX-APIKEY": api_key},
            timeout=_TIMEOUT,
        )
        if not resp.ok:
            frappe.log_error(
                title="Binance API Error",
                message=f"{resp.status_code}: {resp.text[:300]} — {path}",
            )
            frappe.throw(_("Binance API error ({0}): {1}").format(resp.status_code, resp.text[:200]))
        return resp.json()
    except requests.RequestException as e:
        frappe.log_error(title="Binance Network Error", message=str(e))
        frappe.throw(_("Binance network error: {0}").format(str(e)))


def _delete(path: str, params: dict, api_key: str, api_secret: str) -> dict:
    """Authenticated DELETE to Binance REST API (cancel order)."""
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign_query(params, api_secret)
    try:
        resp = requests.delete(
            f"{_REST_BASE}{path}",
            params=params,
            headers={"X-MBX-APIKEY": api_key},
            timeout=_TIMEOUT,
        )
        if not resp.ok:
            frappe.log_error(title="Binance API Error", message=f"{resp.status_code}: {resp.text[:200]}")
            frappe.throw(_("Binance cancel error ({0})").format(resp.status_code))
        return resp.json()
    except requests.RequestException as e:
        frappe.log_error(title="Binance Network Error", message=str(e))
        frappe.throw(_("Binance network error: {0}").format(str(e)))


# ---------------------------------------------------------------------------
# Symbol helpers
# ---------------------------------------------------------------------------

def to_binance_symbol(symbol: str) -> str:
    """
    Convert flat symbol to Binance format.
    BTCINR  → BTCUSDT  (Binance has no INR pairs)
    ETHINR  → ETHUSDT
    BTCUSDT → BTCUSDT  (already correct)
    """
    s = symbol.upper()
    if s.endswith("INR"):
        return s[:-3] + "USDT"
    return s


def _get_usdt_inr_rate() -> float:
    """Fetch live USDT/INR rate from CoinDCX public ticker for INR display."""
    try:
        resp = requests.get(
            "https://api.coindcx.com/exchange/ticker", timeout=6
        )
        if resp.ok:
            for t in resp.json():
                if t.get("market") == "USDTINR":
                    return flt(t.get("last_price", 85)) or 85
    except Exception:
        pass
    return 85.0  # fallback rate


# ---------------------------------------------------------------------------
# Public market data
# ---------------------------------------------------------------------------

def get_ticker(symbol: str = None) -> list | dict:
    """
    Get 24h ticker stats.
    symbol: Binance symbol like 'BTCUSDT'. If None, returns all.
    """
    params = {}
    if symbol:
        params["symbol"] = to_binance_symbol(symbol)
    return _get("/api/v3/ticker/24hr", params)


def get_price(symbol: str) -> float:
    """Get current price for a symbol."""
    data = _get("/api/v3/ticker/price", {"symbol": to_binance_symbol(symbol)})
    return flt(data.get("price", 0))


def get_orderbook(symbol: str, limit: int = 20) -> dict:
    """
    Get order book for a symbol.
    Returns: {"bids": [[price, qty], ...], "asks": [[price, qty], ...]}
    """
    data = _get("/api/v3/depth", {
        "symbol": to_binance_symbol(symbol),
        "limit": limit,
    })
    return {
        "bids": [[flt(p), flt(q)] for p, q in data.get("bids", [])],
        "asks": [[flt(p), flt(q)] for p, q in data.get("asks", [])],
    }


def get_candles(symbol: str, interval: str = "1h", limit: int = 500) -> list[dict]:
    """
    Fetch OHLCV candles from Binance.
    interval: 1m, 5m, 15m, 30m, 1h, 4h, 1d
    Returns list of dicts: {time, open, high, low, close, volume}
    """
    data = _get("/api/v3/klines", {
        "symbol": to_binance_symbol(symbol),
        "interval": interval,
        "limit": limit,
    })
    out = []
    for c in (data or []):
        try:
            out.append({
                "time":   int(c[0] / 1000),
                "open":   flt(c[1]),
                "high":   flt(c[2]),
                "low":    flt(c[3]),
                "close":  flt(c[4]),
                "volume": flt(c[5]),
            })
        except Exception:
            continue
    return out


def get_candles_inr(symbol: str, interval: str = "1h", limit: int = 500) -> list[dict]:
    """
    Fetch candles and convert USDT prices to INR using live USDT/INR rate.
    Use this for INR symbols like BTCINR when the user has Binance as exchange.
    """
    candles = get_candles(symbol, interval, limit)
    rate = _get_usdt_inr_rate()
    for c in candles:
        c["open"]  = round(c["open"]  * rate, 4)
        c["high"]  = round(c["high"]  * rate, 4)
        c["low"]   = round(c["low"]   * rate, 4)
        c["close"] = round(c["close"] * rate, 4)
    return candles


def get_exchange_info(symbol: str = None) -> dict:
    """Get trading rules and symbol info."""
    params = {}
    if symbol:
        params["symbol"] = to_binance_symbol(symbol)
    return _get("/api/v3/exchangeInfo", params)


# ---------------------------------------------------------------------------
# Authenticated — account
# ---------------------------------------------------------------------------

def get_balances(user: str) -> list[dict]:
    """
    Return all non-zero wallet balances.
    Each entry: {currency, balance, locked_balance}
    """
    cred = _get_binance_credential(user)
    data = _get("/api/v3/account", {}, cred["api_key"], cred["api_secret"])
    out = []
    for b in data.get("balances", []):
        free   = flt(b.get("free", 0))
        locked = flt(b.get("locked", 0))
        if free + locked > 0:
            out.append({
                "currency":       b["asset"],
                "balance":        free + locked,
                "locked_balance": locked,
                "free":           free,
            })
    return out


def get_usdt_balance(user: str) -> float:
    """Return available USDT balance."""
    for b in get_balances(user):
        if b["currency"] == "USDT":
            return b["free"]
    return 0.0


def get_inr_equivalent_balance(user: str) -> float:
    """Return USDT balance converted to INR using live rate."""
    usdt = get_usdt_balance(user)
    return round(usdt * _get_usdt_inr_rate(), 2)


def get_account_info(user: str) -> dict:
    """Return full account info from Binance."""
    cred = _get_binance_credential(user)
    return _get("/api/v3/account", {}, cred["api_key"], cred["api_secret"])


def verify_credential(user: str) -> dict:
    """
    Test Binance credential and return account summary.
    Updates CT Exchange Credential with verified balance.
    """
    try:
        info    = get_account_info(user)
        usdt_bal = get_usdt_balance(user)
        inr_bal  = round(usdt_bal * _get_usdt_inr_rate(), 2)

        cred_name = frappe.db.get_value(
            "CT Exchange Credential",
            {"user": user, "is_active": 1, "exchange": "Binance"},
            "name",
        )
        if cred_name:
            frappe.db.set_value(
                "CT Exchange Credential",
                cred_name,
                {
                    "last_verified":        frappe.utils.now_datetime(),
                    "verified_balance_inr": inr_bal,
                },
            )
        return {
            "valid":          True,
            "usdt_balance":   usdt_bal,
            "balance_inr":    inr_bal,
            "account_type":   info.get("accountType", "SPOT"),
            "can_trade":      info.get("canTrade", False),
        }
    except Exception as e:
        return {"valid": False, "balance_inr": 0.0, "error": str(e)[:200]}


# ---------------------------------------------------------------------------
# Authenticated — orders
# ---------------------------------------------------------------------------

def place_order(
    user: str,
    symbol: str,
    side: str,
    order_type: str = "MARKET",
    quantity: float = None,
    quote_order_qty: float = None,
    price: float = None,
) -> dict:
    """
    Place a spot order on Binance.

    symbol:         e.g. 'BTCUSDT' or 'BTCINR' (auto-converted to USDT)
    side:           'BUY' | 'SELL'
    order_type:     'MARKET' | 'LIMIT'
    quantity:       base asset quantity (e.g. BTC amount)
    quote_order_qty: quote asset amount (e.g. USDT to spend) — for MARKET BUY
    price:          required for LIMIT orders
    Returns the Binance order response dict.
    """
    cred = _get_binance_credential(user)
    bnb_symbol = to_binance_symbol(symbol)
    params = {
        "symbol":    bnb_symbol,
        "side":      side.upper(),
        "type":      order_type.upper(),
    }
    if order_type.upper() == "MARKET":
        if quote_order_qty:
            params["quoteOrderQty"] = flt(quote_order_qty)
        elif quantity:
            params["quantity"] = flt(quantity)
        else:
            frappe.throw(_("Provide either quantity or quoteOrderQty for MARKET order."))
    else:
        if not price or not quantity:
            frappe.throw(_("Price and quantity are required for LIMIT orders."))
        params["quantity"]   = flt(quantity)
        params["price"]      = flt(price)
        params["timeInForce"] = "GTC"

    result = _post("/api/v3/order", params, cred["api_key"], cred["api_secret"])

    # Normalize response to match CoinDCX format expected by trader.py
    fills     = result.get("fills", [])
    avg_price = (
        sum(flt(f["price"]) * flt(f["qty"]) for f in fills) / sum(flt(f["qty"]) for f in fills)
        if fills else flt(result.get("price", 0))
    )
    return {
        "id":        result.get("orderId", ""),
        "status":    result.get("status", ""),
        "symbol":    result.get("symbol", ""),
        "side":      result.get("side", ""),
        "avg_price": avg_price,
        "quantity":  flt(result.get("executedQty", quantity)),
        "raw":       result,
    }


def cancel_order(user: str, symbol: str, order_id: str) -> dict:
    """Cancel an open Binance order."""
    cred = _get_binance_credential(user)
    return _delete(
        "/api/v3/order",
        {"symbol": to_binance_symbol(symbol), "orderId": order_id},
        cred["api_key"], cred["api_secret"],
    )


def get_order_status(user: str, symbol: str, order_id: str) -> dict:
    """Fetch status of a Binance order."""
    cred = _get_binance_credential(user)
    return _get(
        "/api/v3/order",
        {"symbol": to_binance_symbol(symbol), "orderId": order_id},
        cred["api_key"], cred["api_secret"],
    )


def get_open_orders(user: str, symbol: str = None) -> list[dict]:
    """Return all open orders, optionally filtered by symbol."""
    cred = _get_binance_credential(user)
    params = {}
    if symbol:
        params["symbol"] = to_binance_symbol(symbol)
    return _get("/api/v3/openOrders", params, cred["api_key"], cred["api_secret"])


def get_trade_history(user: str, symbol: str, limit: int = 100) -> list[dict]:
    """Return authenticated trade history for a symbol."""
    cred = _get_binance_credential(user)
    return _get(
        "/api/v3/myTrades",
        {"symbol": to_binance_symbol(symbol), "limit": min(limit, 1000)},
        cred["api_key"], cred["api_secret"],
    )


# ---------------------------------------------------------------------------
# Server time / ping
# ---------------------------------------------------------------------------

def ping() -> bool:
    """Return True if Binance API is reachable."""
    try:
        _get("/api/v3/ping")
        return True
    except Exception:
        return False


def get_server_time() -> int:
    """Return Binance server time in milliseconds."""
    data = _get("/api/v3/time")
    return data.get("serverTime", int(time.time() * 1000))

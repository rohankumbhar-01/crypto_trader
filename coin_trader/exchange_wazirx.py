"""
Coin Trader · WazirX Exchange Adapter
======================================
Implements the same interface as exchange.py (CoinDCX) and exchange_binance.py.

WazirX API docs: https://docs.wazirx.com/

Key details:
- Symbol format: lowercase — btcinr, ethinr (native INR pairs, no conversion needed)
- Signature: HMAC-SHA256 over URL-encoded query string (same as Binance)
- Balance returned in INR / BTC / coin units
- Kline format: [openTime, open, high, low, close, volume]
- WazirX only supports LIMIT orders (no MARKET orders via API)
"""

import hashlib
import hmac
import time

import frappe
import requests
from frappe import _
from frappe.utils import flt

_REST_BASE = "https://api.wazirx.com"
_TIMEOUT   = 10


# ---------------------------------------------------------------------------
# Credential helper
# ---------------------------------------------------------------------------

def _get_wazirx_credential(user: str) -> dict:
    name = frappe.db.get_value(
        "CT Exchange Credential",
        {"user": user, "is_active": 1, "exchange": "WazirX"},
        ["name", "api_key", "api_secret"],
        as_dict=True,
    )
    if not name:
        frappe.throw(_("No active WazirX credential found for user {0}.").format(user))
    from frappe.utils.password import get_decrypted_password
    name["api_secret"] = get_decrypted_password(
        "CT Exchange Credential", name["name"], "api_secret"
    )
    return name


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------

def _sign_query(params: dict, api_secret: str) -> str:
    """WazirX uses HMAC-SHA256 over the URL-encoded query string."""
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(
        api_secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _get(path: str, params: dict = None, api_key: str = None, api_secret: str = None) -> dict | list:
    """Authenticated or public GET to WazirX REST API."""
    params = params or {}
    headers = {}
    if api_key:
        params["recvWindow"] = 10000
        params["timestamp"]  = int(time.time() * 1000)
        params["signature"]  = _sign_query(params, api_secret)
        headers["X-Api-Key"] = api_key
    try:
        resp = requests.get(
            f"{_REST_BASE}{path}", params=params, headers=headers, timeout=_TIMEOUT
        )
        if not resp.ok:
            frappe.log_error(
                title="WazirX API Error",
                message=f"{resp.status_code}: {resp.text[:300]} — {path}",
            )
            frappe.throw(_("WazirX API error ({0}): {1}").format(resp.status_code, resp.text[:200]))
        return resp.json()
    except requests.RequestException as e:
        frappe.log_error(title="WazirX Network Error", message=str(e))
        frappe.throw(_("WazirX network error: {0}").format(str(e)))


def _post(path: str, params: dict, api_key: str, api_secret: str) -> dict:
    """Authenticated POST to WazirX REST API."""
    params["recvWindow"] = 10000
    params["timestamp"]  = int(time.time() * 1000)
    params["signature"]  = _sign_query(params, api_secret)
    try:
        resp = requests.post(
            f"{_REST_BASE}{path}",
            params=params,
            headers={"X-Api-Key": api_key},
            timeout=_TIMEOUT,
        )
        if not resp.ok:
            frappe.log_error(
                title="WazirX API Error",
                message=f"{resp.status_code}: {resp.text[:300]} — {path}",
            )
            frappe.throw(_("WazirX API error ({0}): {1}").format(resp.status_code, resp.text[:200]))
        return resp.json()
    except requests.RequestException as e:
        frappe.log_error(title="WazirX Network Error", message=str(e))
        frappe.throw(_("WazirX network error: {0}").format(str(e)))


def _delete(path: str, params: dict, api_key: str, api_secret: str) -> dict:
    """Authenticated DELETE to WazirX REST API (cancel order)."""
    params["recvWindow"] = 10000
    params["timestamp"]  = int(time.time() * 1000)
    params["signature"]  = _sign_query(params, api_secret)
    try:
        resp = requests.delete(
            f"{_REST_BASE}{path}",
            params=params,
            headers={"X-Api-Key": api_key},
            timeout=_TIMEOUT,
        )
        if not resp.ok:
            frappe.log_error(title="WazirX API Error", message=f"{resp.status_code}: {resp.text[:200]}")
            frappe.throw(_("WazirX cancel error ({0})").format(resp.status_code))
        return resp.json()
    except requests.RequestException as e:
        frappe.log_error(title="WazirX Network Error", message=str(e))
        frappe.throw(_("WazirX network error: {0}").format(str(e)))


# ---------------------------------------------------------------------------
# Symbol helpers
# ---------------------------------------------------------------------------

def to_wazirx_symbol(symbol: str) -> str:
    """
    Convert symbol to WazirX lowercase format.
    BTCINR -> btcinr, ETHINR -> ethinr, btcinr -> btcinr
    """
    return symbol.lower().strip()


# ---------------------------------------------------------------------------
# Public market data
# ---------------------------------------------------------------------------

def get_ticker(symbol: str = None) -> list | dict:
    """
    Get 24h ticker stats.
    symbol: e.g. 'BTCINR' or 'btcinr'. If None, returns all (list).
    """
    if symbol:
        return _get("/sapi/v1/ticker/24hr", {"symbol": to_wazirx_symbol(symbol)})
    return _get("/sapi/v1/tickers/24hr")


def get_price(symbol: str) -> float:
    """Get current last price for a symbol."""
    data = get_ticker(symbol)
    if isinstance(data, dict):
        return flt(data.get("lastPrice", 0))
    return 0.0


def get_orderbook(symbol: str, limit: int = 20) -> dict:
    """
    Get order book for a symbol.
    Returns: {"bids": [[price, qty], ...], "asks": [[price, qty], ...]}
    WazirX returns 20 entries by default; no limit param accepted.
    """
    data = _get("/sapi/v1/depth", {"symbol": to_wazirx_symbol(symbol)})
    bids = [[flt(p), flt(q)] for p, q in data.get("bids", [])][:limit]
    asks = [[flt(p), flt(q)] for p, q in data.get("asks", [])][:limit]
    return {"bids": bids, "asks": asks}


def get_candles(symbol: str, interval: str = "1h", limit: int = 500) -> list[dict]:
    """
    Fetch OHLCV candles from WazirX.
    interval: 1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d, 1w
    Returns list of dicts: {time, open, high, low, close, volume}
    Prices are already in INR for INR pairs — no conversion needed.
    """
    data = _get("/sapi/v1/klines", {
        "symbol":   to_wazirx_symbol(symbol),
        "interval": interval,
        "limit":    min(limit, 1000),
    })
    out = []
    for c in (data or []):
        try:
            # WazirX kline: [openTime(seconds), open, high, low, close, volume]
            out.append({
                "time":   int(c[0]),
                "open":   flt(c[1]),
                "high":   flt(c[2]),
                "low":    flt(c[3]),
                "close":  flt(c[4]),
                "volume": flt(c[5]),
            })
        except Exception:
            continue
    return out


def get_exchange_info() -> dict:
    """Get trading rules and all available symbols."""
    return _get("/sapi/v1/exchangeInfo")


def get_server_time() -> int:
    """Return WazirX server time in milliseconds."""
    data = _get("/sapi/v1/time")
    return data.get("serverTime", int(time.time() * 1000))


def ping() -> bool:
    """Return True if WazirX API is reachable."""
    try:
        _get("/sapi/v1/time")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Authenticated — account
# ---------------------------------------------------------------------------

def get_balances(user: str) -> list[dict]:
    """
    Return all non-zero wallet balances.
    Each entry: {currency, balance, locked_balance, free}
    """
    cred = _get_wazirx_credential(user)
    data = _get("/sapi/v1/account", {}, cred["api_key"], cred["api_secret"])
    out  = []
    for asset, info in data.get("assets", {}).items():
        free   = flt(info.get("free", 0))
        locked = flt(info.get("locked", 0))
        if free + locked > 0:
            out.append({
                "currency":       asset.upper(),
                "balance":        free + locked,
                "locked_balance": locked,
                "free":           free,
            })
    return out


def get_inr_balance(user: str) -> float:
    """Return available INR balance (free only)."""
    for b in get_balances(user):
        if b["currency"] == "INR":
            return b["free"]
    return 0.0


def get_account_info(user: str) -> dict:
    """Return full account info from WazirX."""
    cred = _get_wazirx_credential(user)
    return _get("/sapi/v1/account", {}, cred["api_key"], cred["api_secret"])


def verify_credential(user: str) -> dict:
    """
    Test WazirX credential and return account summary.
    Updates CT Exchange Credential with verified balance.
    """
    try:
        info    = get_account_info(user)
        inr_bal = get_inr_balance(user)

        cred_name = frappe.db.get_value(
            "CT Exchange Credential",
            {"user": user, "is_active": 1, "exchange": "WazirX"},
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
            "valid":       True,
            "balance_inr": inr_bal,
            "can_trade":   True,
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
    order_type: str = "limit",
    quantity: float = None,
    price: float = None,
) -> dict:
    """
    Place a spot order on WazirX.

    symbol:     e.g. 'BTCINR' (auto-lowercased)
    side:       'buy' | 'sell'
    order_type: 'limit' | 'stop_limit' | 'limit_maker'
    quantity:   base asset quantity
    price:      required for all WazirX order types
    Returns normalised dict: {id, status, symbol, side, avg_price, quantity}

    Note: WazirX does NOT support pure market orders — limit orders are used.
    Caller should pass current market price as `price` for a market-equivalent fill.
    """
    if not price:
        frappe.throw(_("Price is required for WazirX orders (no market order support)."))
    if not quantity:
        frappe.throw(_("Quantity is required for WazirX orders."))

    cred = _get_wazirx_credential(user)
    params = {
        "symbol":   to_wazirx_symbol(symbol),
        "side":     side.lower(),
        "type":     order_type.lower(),
        "quantity": flt(quantity),
        "price":    flt(price),
    }

    result = _post("/sapi/v1/order", params, cred["api_key"], cred["api_secret"])

    return {
        "id":        str(result.get("id", "")),
        "status":    result.get("status", ""),
        "symbol":    result.get("symbol", symbol),
        "side":      result.get("side", side),
        "avg_price": flt(result.get("price", price)),
        "quantity":  flt(result.get("origQty", quantity)),
        "raw":       result,
    }


def cancel_order(user: str, symbol: str, order_id: str) -> dict:
    """Cancel an open WazirX order."""
    cred = _get_wazirx_credential(user)
    return _delete(
        "/sapi/v1/order",
        {"symbol": to_wazirx_symbol(symbol), "orderId": order_id},
        cred["api_key"], cred["api_secret"],
    )


def get_order_status(user: str, symbol: str, order_id: str) -> dict:
    """Fetch status of a WazirX order."""
    cred = _get_wazirx_credential(user)
    return _get(
        "/sapi/v1/order",
        {"symbol": to_wazirx_symbol(symbol), "orderId": order_id},
        cred["api_key"], cred["api_secret"],
    )


def get_open_orders(user: str, symbol: str = None) -> list[dict]:
    """Return all open orders, optionally filtered by symbol."""
    cred = _get_wazirx_credential(user)
    params = {}
    if symbol:
        params["symbol"] = to_wazirx_symbol(symbol)
    return _get("/sapi/v1/openOrders", params, cred["api_key"], cred["api_secret"])


def get_trade_history(user: str, symbol: str, limit: int = 100) -> list[dict]:
    """Return authenticated trade history for a symbol."""
    cred = _get_wazirx_credential(user)
    return _get(
        "/sapi/v1/myTrades",
        {"symbol": to_wazirx_symbol(symbol), "limit": min(limit, 500)},
        cred["api_key"], cred["api_secret"],
    )

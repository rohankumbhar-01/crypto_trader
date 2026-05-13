import hashlib
import hmac
import json
import time

import frappe
from frappe import _
from frappe.utils import cint, flt

import httpx

# ---------------------------------------------------------------------------
# Base URLs
# ---------------------------------------------------------------------------
_REST_BASE = "https://api.coindcx.com"
_PUBLIC_BASE = "https://public.coindcx.com"

# Default HTTP timeout in seconds
_TIMEOUT = 10

# CoinDCX ecode → pair prefix mapping
_ECODE_PREFIX = {"I": "I-", "B": "B-"}


# ---------------------------------------------------------------------------
# Pair format helpers
# ---------------------------------------------------------------------------

def symbol_to_pair(symbol: str, ecode: str = "I") -> str:
	"""
	Convert a CoinDCX symbol to the pair format used by public data endpoints.

	symbol: e.g. 'BTCINR', 'ETHINR', 'BTCUSDT'
	ecode:  'I' for INR markets, 'B' for BTC markets (default 'I')
	Returns: e.g. 'I-BTC_INR', 'B-BTC_USDT'
	"""
	if symbol.endswith("INR"):
		base = symbol[:-3]
		return f"I-{base}_INR"
	if symbol.endswith("USDT"):
		base = symbol[:-4]
		return f"B-{base}_USDT"
	if symbol.endswith("BTC"):
		base = symbol[:-3]
		return f"B-{base}_BTC"
	# Fallback: use ecode prefix and guess
	prefix = _ECODE_PREFIX.get(ecode, "I-")
	return f"{prefix}{symbol}"


def get_pair_ecode(symbol: str) -> str:
	"""Return the ecode for a symbol — 'I' for INR pairs, 'B' for others."""
	return "I" if symbol.endswith("INR") else "B"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_credential(user: str) -> dict:
	"""Return the active CT Exchange Credential for the given user."""
	name = frappe.db.get_value(
		"CT Exchange Credential",
		{"user": user, "is_active": 1},
		["name", "api_key", "api_secret"],
		as_dict=True,
	)
	if not name:
		frappe.throw(_("No active CoinDCX credential found for user {0}.").format(user))
	# Decrypt password field
	from frappe.utils.password import get_decrypted_password
	name["api_secret"] = get_decrypted_password("CT Exchange Credential", name["name"], "api_secret")
	return name


def _sign(payload: dict, api_secret: str) -> str:
	"""Generate HMAC-SHA256 signature over the JSON-encoded payload."""
	body = json.dumps(payload, separators=(",", ":"))
	return hmac.new(
		api_secret.encode("utf-8"),
		body.encode("utf-8"),
		hashlib.sha256,
	).hexdigest()


def _auth_headers(api_key: str, signature: str) -> dict:
	return {
		"Content-Type": "application/json",
		"X-AUTH-APIKEY": api_key,
		"X-AUTH-SIGNATURE": signature,
	}


def _post(path: str, payload: dict, api_key: str, api_secret: str) -> dict:
	"""Authenticated POST to the CoinDCX REST API."""
	payload["timestamp"] = cint(time.time() * 1000)
	sig = _sign(payload, api_secret)
	headers = _auth_headers(api_key, sig)
	url = f"{_REST_BASE}{path}"
	try:
		resp = httpx.post(url, json=payload, headers=headers, timeout=_TIMEOUT)
		resp.raise_for_status()
		return resp.json()
	except httpx.HTTPStatusError as e:
		frappe.log_error(
			title="CoinDCX API Error",
			message=f"{e.response.status_code} {e.response.text} — {path}",
		)
		frappe.throw(_("CoinDCX API error ({0}): {1}").format(e.response.status_code, e.response.text))
	except httpx.RequestError as e:
		frappe.log_error(title="CoinDCX Network Error", message=str(e))
		frappe.throw(_("CoinDCX network error: {0}").format(str(e)))


def _get_public(path: str, params: dict | None = None) -> list | dict:
	"""Unauthenticated GET from the public CoinDCX endpoint."""
	url = f"{_PUBLIC_BASE}{path}"
	try:
		resp = httpx.get(url, params=params, timeout=_TIMEOUT)
		resp.raise_for_status()
		return resp.json()
	except httpx.HTTPStatusError as e:
		frappe.log_error(
			title="CoinDCX Public API Error",
			message=f"{e.response.status_code} {e.response.text} — {path}",
		)
		frappe.throw(_("CoinDCX public API error ({0})").format(e.response.status_code))
	except httpx.RequestError as e:
		frappe.log_error(title="CoinDCX Network Error", message=str(e))
		frappe.throw(_("CoinDCX network error: {0}").format(str(e)))


def _get_exchange(path: str) -> list | dict:
	"""Unauthenticated GET from api.coindcx.com (ticker, markets)."""
	url = f"{_REST_BASE}{path}"
	try:
		resp = httpx.get(url, timeout=_TIMEOUT)
		resp.raise_for_status()
		return resp.json()
	except httpx.HTTPStatusError as e:
		frappe.log_error(title="CoinDCX API Error", message=f"{e.response.status_code} — {path}")
		frappe.throw(_("CoinDCX API error ({0})").format(e.response.status_code))
	except httpx.RequestError as e:
		frappe.log_error(title="CoinDCX Network Error", message=str(e))
		frappe.throw(_("CoinDCX network error: {0}").format(str(e)))


# ---------------------------------------------------------------------------
# Public market data
# ---------------------------------------------------------------------------

def get_ticker() -> list[dict]:
	"""Return full ticker for all markets."""
	return _get_exchange("/exchange/ticker")


def get_ticker_for(pair: str) -> dict | None:
	"""Return ticker for a single pair (e.g. 'B-BTC_INR')."""
	tickers = get_ticker()
	for t in tickers:
		if t.get("market") == pair:
			return t
	return None


def get_markets() -> list[str]:
	"""Return list of active market symbols."""
	return _get_exchange("/exchange/v1/markets")


def get_markets_details() -> list[dict]:
	"""Return detailed info for all markets (min/max qty, precision, etc.)."""
	return _get_exchange("/exchange/v1/markets_details")


def get_orderbook(pair: str) -> dict:
	"""
	Return orderbook snapshot for a pair.
	pair example: 'B-BTC_INR'
	Returns: {"bids": {...}, "asks": {...}}
	"""
	return _get_public("/market_data/orderbook", {"pair": pair})


def get_candles(pair: str, interval: str, limit: int = 500,
				start_time: int | None = None, end_time: int | None = None) -> list[dict]:
	"""
	Fetch OHLCV candles.

	pair:     e.g. 'B-BTC_INR'
	interval: 1m | 5m | 15m | 30m | 1h | 2h | 4h | 6h | 8h | 1d | 3d | 1w | 1M
	limit:    max candles to return (up to 500)
	Returns list of dicts: {open, high, low, close, volume, time}
	"""
	params = {"pair": pair, "interval": interval, "limit": limit}
	if start_time:
		params["startTime"] = start_time
	if end_time:
		params["endTime"] = end_time
	return _get_public("/market_data/candles", params)


def get_trade_history(pair: str, limit: int = 30) -> list[dict]:
	"""Return recent public trade history for a pair."""
	return _get_public("/market_data/trade_history", {"pair": pair, "limit": min(limit, 500)})


# ---------------------------------------------------------------------------
# Authenticated — account
# ---------------------------------------------------------------------------

def get_balances(user: str) -> list[dict]:
	"""
	Return all wallet balances for the user.
	Each entry: {currency, balance, locked_balance}
	"""
	cred = _get_credential(user)
	return _post("/exchange/v1/users/balances", {}, cred["api_key"], cred["api_secret"])


def get_inr_balance(user: str) -> float:
	"""Return available INR balance (balance - locked_balance)."""
	balances = get_balances(user)
	for b in balances:
		if b.get("currency") == "INR":
			return flt(b.get("balance", 0)) - flt(b.get("locked_balance", 0))
	return 0.0


def get_user_info(user: str) -> dict:
	"""Return CoinDCX account info for the user."""
	cred = _get_credential(user)
	return _post("/exchange/v1/users/info", {}, cred["api_key"], cred["api_secret"])


def verify_credential(user: str) -> dict:
	"""
	Test credential validity by fetching user info.
	Updates CT Exchange Credential with verified balance and timestamp.
	Returns {"valid": bool, "balance_inr": float, "coindcx_id": str}
	"""
	try:
		info = get_user_info(user)
		balance = get_inr_balance(user)
		cred_name = frappe.db.get_value(
			"CT Exchange Credential", {"user": user, "is_active": 1}, "name"
		)
		if cred_name:
			frappe.db.set_value(
				"CT Exchange Credential",
				cred_name,
				{
					"last_verified": frappe.utils.now_datetime(),
					"verified_balance_inr": balance,
				},
			)
		return {
			"valid": True,
			"balance_inr": balance,
			"coindcx_id": info.get("coindcx_id", ""),
		}
	except Exception:
		return {"valid": False, "balance_inr": 0.0, "coindcx_id": ""}


# ---------------------------------------------------------------------------
# Authenticated — orders
# ---------------------------------------------------------------------------

def place_order(
	user: str,
	market: str,
	side: str,
	order_type: str,
	quantity: float,
	price: float | None = None,
	client_order_id: str | None = None,
) -> dict:
	"""
	Place a spot order on CoinDCX.

	market:     e.g. 'BTCINR'
	side:       'buy' | 'sell'
	order_type: 'limit_order' | 'market_order'
	quantity:   total quantity to buy/sell
	price:      required for limit_order
	Returns the order dict from CoinDCX.
	"""
	cred = _get_credential(user)
	payload = {
		"market": market,
		"side": side,
		"order_type": order_type,
		"total_quantity": flt(quantity),
	}
	if order_type == "limit_order":
		if not price:
			frappe.throw(_("Price is required for limit orders."))
		payload["price_per_unit"] = flt(price)
	if client_order_id:
		payload["client_order_id"] = client_order_id
	return _post("/exchange/v1/orders/create", payload, cred["api_key"], cred["api_secret"])


def cancel_order(user: str, order_id: str) -> dict:
	"""Cancel an open order by CoinDCX order ID."""
	cred = _get_credential(user)
	return _post(
		"/exchange/v1/orders/cancel",
		{"id": order_id},
		cred["api_key"],
		cred["api_secret"],
	)


def get_order_status(user: str, order_id: str) -> dict:
	"""Fetch status of a single order."""
	cred = _get_credential(user)
	return _post(
		"/exchange/v1/orders/status",
		{"id": order_id},
		cred["api_key"],
		cred["api_secret"],
	)


def get_active_orders(user: str, market: str, side: str | None = None) -> list[dict]:
	"""Return all open/partially-filled orders for a market."""
	cred = _get_credential(user)
	payload = {"market": market}
	if side:
		payload["side"] = side
	return _post("/exchange/v1/orders/active_orders", payload, cred["api_key"], cred["api_secret"])


def cancel_all_orders(user: str, market: str, side: str | None = None) -> dict:
	"""Cancel all open orders for a market."""
	cred = _get_credential(user)
	payload = {"market": market}
	if side:
		payload["side"] = side
	return _post("/exchange/v1/orders/cancel_all", payload, cred["api_key"], cred["api_secret"])


def get_trade_history_auth(
	user: str,
	limit: int = 100,
	from_timestamp: int | None = None,
	to_timestamp: int | None = None,
) -> list[dict]:
	"""Return authenticated trade history for the user."""
	cred = _get_credential(user)
	payload = {"limit": min(limit, 500)}
	if from_timestamp:
		payload["from_timestamp"] = from_timestamp
	if to_timestamp:
		payload["to_timestamp"] = to_timestamp
	return _post("/exchange/v1/orders/trade_history", payload, cred["api_key"], cred["api_secret"])


# ---------------------------------------------------------------------------
# Whitelisted API — called from JS / other Frappe contexts
# ---------------------------------------------------------------------------

@frappe.whitelist()
def verify_exchange_credential(user: str | None = None) -> dict:
	"""Verify the active exchange credential for a user. Called from CT Exchange Credential form."""
	user = user or frappe.session.user
	if frappe.session.user != user and not frappe.has_role("System Manager"):
		frappe.throw(_("Not permitted."), frappe.PermissionError)
	return verify_credential(user)

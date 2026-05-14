"""
Dashboard API — server-side proxy + aggregator for /ct-dashboard
=================================================================

Centralised endpoints used by the ultra dashboard. All calls run server-side
so the browser does not hit CoinDCX directly (avoids CORS, hides API keys,
and lets us cache aggressively).

Endpoints:
  get_snapshot()            — one-shot blob: balance + positions + KPIs + market
  get_market_ticker()       — public CoinDCX /exchange/ticker (cached 5s)
  get_orderbook(pair)       — public order book for one pair
  get_candles_data(symbol, interval, limit) — OHLCV for charting
"""

import json
import time

import frappe
import requests
from frappe.utils import flt, cint, now_datetime

# ---------------------------------------------------------------------------
# Module-level caches (per worker process)
# ---------------------------------------------------------------------------

_CACHE = {
    "ticker":       {"data": None, "ts": 0},
    "markets":      {"data": None, "ts": 0},
    "orderbook":    {},       # pair -> {data, ts}
}

_TICKER_TTL_S = 5
_OB_TTL_S     = 4
_MARKETS_TTL  = 600

_CDX_TICKER     = "https://api.coindcx.com/exchange/ticker"
_CDX_OB         = "https://public.coindcx.com/market_data/orderbook"
_CDX_CANDLES    = "https://public.coindcx.com/market_data/candles"
_CDX_MARKETS    = "https://api.coindcx.com/exchange/v1/markets_details"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _http_get(url, params=None, timeout=8):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        frappe.log_error(title="CoinDCX HTTP", message=f"{url} :: {e}")
    return None


def _norm_market_key(symbol):
    """
    CoinDCX /exchange/ticker uses FLAT market keys (e.g. "BTCINR", "ETHINR").
    Strip any B-/I- prefix and underscores. "B-BTC_INR" -> "BTCINR"
    """
    if not symbol:
        return ""
    s = symbol.strip().upper()
    # Strip exchange prefix
    if len(s) > 2 and s[1] == "-":
        s = s[2:]
    return s.replace("_", "")


def _norm_pair(symbol):
    """
    CoinDCX /market_data/* candle endpoints use exchange-prefixed pairs.
    INR pairs use "I-" prefix:  BTCINR  -> I-BTC_INR
    USDT/other pairs use "B-":  BTCUSDT -> B-BTC_USDT
    """
    if not symbol:
        return ""
    s = symbol.strip().upper()
    # Already normalised (has dash and underscore)
    if "-" in s and "_" in s:
        return s
    # Split base from quote currency
    for q in ("USDT", "USDC", "INR", "BTC", "ETH", "BNB", "USD"):
        if s.endswith(q) and len(s) > len(q):
            base = s[:-len(q)]
            prefix = "I" if q == "INR" else "B"
            return f"{prefix}-{base}_{q}"
    return f"B-{s}"


# ---------------------------------------------------------------------------
# Market data — public ticker (heavily cached)
# ---------------------------------------------------------------------------

def _fetch_ticker_cached():
    now_t = time.time()
    c = _CACHE["ticker"]
    if c["data"] and (now_t - c["ts"]) < _TICKER_TTL_S:
        return c["data"]
    data = _http_get(_CDX_TICKER)
    if data:
        _CACHE["ticker"] = {"data": data, "ts": now_t}
        return data
    return c["data"] or []


@frappe.whitelist()
def get_market_ticker():
    """Return ticker array routed to the user's active exchange."""
    user = frappe.session.user
    try:
        from coin_trader.exchange import get_active_exchange
        exch = get_active_exchange(user)

        if exch == "Binance":
            from coin_trader.exchange_binance import get_ticker as bnb_ticker, _get_usdt_inr_rate
            bnb_data = bnb_ticker() or []
            rate = _get_usdt_inr_rate()
            out = []
            for t in bnb_data:
                sym = t.get("symbol", "")
                if sym.endswith("USDT"):
                    base = sym[:-4]
                    inr_price = round(flt(t.get("lastPrice", 0)) * rate, 4)
                    out.append({
                        "market":         base + "INR",
                        "last_price":     inr_price,
                        "high":           round(flt(t.get("highPrice", 0)) * rate, 4),
                        "low":            round(flt(t.get("lowPrice", 0)) * rate, 4),
                        "volume":         flt(t.get("volume", 0)),
                        "bid":            round(flt(t.get("bidPrice", 0)) * rate, 4),
                        "ask":            round(flt(t.get("askPrice", 0)) * rate, 4),
                        "change_24_hour": flt(t.get("priceChangePercent", 0)),
                    })
            return out

        if exch == "WazirX":
            from coin_trader.exchange_wazirx import get_ticker as wzx_ticker
            wzx_data = wzx_ticker() or []
            out = []
            for t in wzx_data:
                sym = (t.get("symbol") or "").upper()
                if not sym:
                    continue
                out.append({
                    "market":         sym,
                    "last_price":     flt(t.get("lastPrice", 0)),
                    "high":           flt(t.get("highPrice", 0)),
                    "low":            flt(t.get("lowPrice", 0)),
                    "volume":         flt(t.get("volume", 0)),
                    "bid":            flt(t.get("bidPrice", 0)),
                    "ask":            flt(t.get("askPrice", 0)),
                    "change_24_hour": round(
                        (flt(t.get("lastPrice", 0)) - flt(t.get("openPrice", 0)))
                        / (flt(t.get("openPrice", 1)) or 1) * 100, 3
                    ),
                })
            return out

    except Exception:
        pass
    return _fetch_ticker_cached()


# ---------------------------------------------------------------------------
# Order book proxy (cached per pair)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_orderbook(pair):
    """Return order book for a single pair. Routes to Binance or CoinDCX based on active credential."""
    if not pair:
        return {"bids": [], "asks": []}

    user = frappe.session.user
    try:
        from coin_trader.exchange import get_active_exchange
        exch = get_active_exchange(user)

        if exch == "Binance":
            from coin_trader.exchange_binance import get_orderbook as bnb_ob, _get_usdt_inr_rate
            sym = pair.strip().upper()
            if "-" in sym and "_" in sym:
                sym = sym.split("-", 1)[1].replace("_", "")
            is_inr = sym.endswith("INR")
            bnb_sym = sym[:-3] + "USDT" if is_inr else sym
            ob = bnb_ob(bnb_sym)
            if is_inr:
                rate = _get_usdt_inr_rate()
                ob["bids"] = [[round(p * rate, 4), q] for p, q in ob.get("bids", [])]
                ob["asks"] = [[round(p * rate, 4), q] for p, q in ob.get("asks", [])]
            return ob

        if exch == "WazirX":
            from coin_trader.exchange_wazirx import get_orderbook as wzx_ob
            sym = pair.strip().upper()
            if "-" in sym and "_" in sym:
                sym = sym.split("-", 1)[1].replace("_", "")
            return wzx_ob(sym)

    except Exception:
        pass  # Fall through to CoinDCX

    pair = _norm_pair(pair)
    now_t = time.time()
    entry = _CACHE["orderbook"].get(pair)
    if entry and (now_t - entry["ts"]) < _OB_TTL_S:
        return entry["data"]

    data = _http_get(f"{_CDX_OB}?pair={pair}")
    if not data:
        return {"bids": [], "asks": []}

    # Normalise bid/ask to ordered arrays [[price, qty], ...]
    bids_raw = data.get("bids", {}) if isinstance(data, dict) else {}
    asks_raw = data.get("asks", {}) if isinstance(data, dict) else {}

    if isinstance(bids_raw, dict):
        bids = sorted(
            [[flt(p), flt(q)] for p, q in bids_raw.items()],
            key=lambda x: -x[0],
        )[:20]
    elif isinstance(bids_raw, list):
        bids = [[flt(x[0]), flt(x[1])] for x in bids_raw[:20]]
    else:
        bids = []

    if isinstance(asks_raw, dict):
        asks = sorted(
            [[flt(p), flt(q)] for p, q in asks_raw.items()],
            key=lambda x: x[0],
        )[:20]
    elif isinstance(asks_raw, list):
        asks = [[flt(x[0]), flt(x[1])] for x in asks_raw[:20]]
    else:
        asks = []

    out = {"bids": bids, "asks": asks}
    _CACHE["orderbook"][pair] = {"data": out, "ts": now_t}
    return out


# ---------------------------------------------------------------------------
# Candle data for chart
# ---------------------------------------------------------------------------

_INTERVAL_MAP = {
    "1m":  "1m",  "5m":  "5m",  "15m": "15m", "30m": "30m",
    "1h":  "1h",  "4h":  "4h",  "1d":  "1d",
}


def _get_usdt_inr_rate():
    """Return cached USDT/INR rate from ticker (fallback 90)."""
    ticker = _fetch_ticker_cached() or []
    for t in ticker:
        if t.get("market") == "USDTINR":
            return flt(t.get("last_price", 90)) or 90
    return 90


@frappe.whitelist()
def get_candles_data(symbol, interval="1h", limit=200):
    """Return candle data formatted for lightweight-charts.

    Routes to Binance, WazirX, or CoinDCX based on user's active credential.
    All exchanges return prices in INR where applicable.
    """
    user  = frappe.session.user
    iv    = _INTERVAL_MAP.get(interval, "1h")
    limit = cint(limit) or 200

    try:
        from coin_trader.exchange import get_active_exchange
        exch = get_active_exchange(user)

        if exch == "Binance":
            from coin_trader.exchange_binance import get_candles_inr, get_candles
            if str(symbol).upper().endswith("INR"):
                return get_candles_inr(symbol, interval=iv, limit=limit)
            return get_candles(symbol, interval=iv, limit=limit)

        if exch == "WazirX":
            from coin_trader.exchange_wazirx import get_candles as wzx_candles
            # WazirX returns native INR prices — no conversion needed
            return wzx_candles(symbol, interval=iv, limit=limit)

    except Exception:
        pass  # Fall through to CoinDCX

    pair  = _norm_pair(symbol)
    data = _http_get(f"{_CDX_CANDLES}?pair={pair}&interval={iv}&limit={limit}")
    if not data or not isinstance(data, list):
        return []

    out = []
    for c in data:
        try:
            out.append({
                "time":   int(c["time"] / 1000) if c.get("time", 0) > 1e12 else int(c.get("time", 0)),
                "open":   flt(c.get("open",   0)),
                "high":   flt(c.get("high",   0)),
                "low":    flt(c.get("low",    0)),
                "close":  flt(c.get("close",  0)),
                "volume": flt(c.get("volume", 0)),
            })
        except Exception:
            continue
    out.sort(key=lambda x: x["time"])
    return out


# ---------------------------------------------------------------------------
# Master snapshot — used by main dashboard poll loop
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_snapshot():
    """
    Single endpoint that returns everything the dashboard needs.
    Called every 5s by the client; cheap because ticker is cached.
    """
    user = frappe.session.user

    snap = {
        "ts":                 now_datetime().isoformat(),
        "user":               user,
        "session_user":       user,
        "inr_balance":        0.0,
        "open_positions":     [],
        "open_positions_count": 0,
        "today_pnl":          0.0,
        "today_trades":       0,
        "future_orders":      [],
        "future_orders_count":0,
        "tracked_symbols":    [],
        "market":             {},        # market_key -> ticker
        "config":             {},
        "dry_run":            True,
        "is_active":          False,
        "ai_provider":        None,
        "model_trained":      False,
    }

    # Config — try exact user match first, then Administrator fallback
    cfg_name = frappe.db.get_value("CT Trading Config", {"user": user, "is_active": 1}, "name")
    if not cfg_name and user != "Administrator":
        cfg_name = frappe.db.get_value("CT Trading Config", {"user": "Administrator", "is_active": 1}, "name")

    if cfg_name:
        cfg = frappe.get_doc("CT Trading Config", cfg_name).as_dict()
        snap["is_active"]  = True
        snap["dry_run"]    = bool(cfg.get("dry_run", 1))
        snap["config"]     = {
            "interval":         cfg.get("candle_interval") or "1h",
            "min_confidence":   cint(cfg.get("min_confidence_pct", 80)),
            "max_positions":    cint(cfg.get("max_positions", 5)),
            "inr_per_trade":    flt(cfg.get("inr_per_trade", 500)),
            "stop_loss_pct":    flt(cfg.get("stop_loss_pct", 1.0)),
            "max_daily_loss":   flt(cfg.get("max_daily_loss_inr", 0)),
        }
        snap["tracked_symbols"] = frappe.get_all(
            "CT Trading Symbol",
            filters={"parent": cfg_name, "enabled": 1},
            fields=["symbol"],
            pluck="symbol",
        )

    # INR balance — routed to CoinDCX or Binance based on active credential
    try:
        from coin_trader.exchange import get_inr_balance_routed
        snap["inr_balance"] = flt(get_inr_balance_routed(user))
    except Exception:
        snap["inr_balance"] = 0.0

    # Open positions
    try:
        positions = frappe.get_all(
            "CT Open Position",
            filters={"user": user, "status": "Open"},
            fields=["name", "symbol", "buy_price", "quantity",
                    "stop_loss", "trailing_stoploss", "target_price",
                    "entry_time", "exchange_order_id", "pnl_inr", "pnl_pct"],
            order_by="entry_time desc",
        )
        # Recompute live PnL using cached ticker
        ticker_data = _fetch_ticker_cached() or []
        ticker_map  = {t.get("market"): t for t in ticker_data}
        for p in positions:
            mkey = _norm_market_key(p["symbol"])
            t    = ticker_map.get(mkey)
            if t:
                cur = flt(t.get("last_price", 0))
                p["current_price"] = cur
                if cur and p["buy_price"]:
                    p["pnl_inr"] = round((cur - flt(p["buy_price"])) * flt(p["quantity"]), 2)
                    p["pnl_pct"] = round((cur - flt(p["buy_price"])) / flt(p["buy_price"]) * 100, 2)
        snap["open_positions"]       = positions
        snap["open_positions_count"] = len(positions)
    except Exception as e:
        frappe.log_error(title="snapshot: positions", message=str(e))

    # Today's PnL + trade count
    try:
        today = now_datetime().date().isoformat()
        row = frappe.db.sql(
            """
            SELECT COALESCE(SUM(pnl_inr),0), COUNT(*)
            FROM `tabCT Trade Log`
            WHERE user=%s AND DATE(trade_time)=%s
            """,
            (user, today),
        )
        if row:
            snap["today_pnl"]    = flt(row[0][0])
            snap["today_trades"] = cint(row[0][1])
    except Exception as e:
        frappe.log_error(title="snapshot: today pnl", message=str(e))

    # Future buy orders
    try:
        fo = frappe.get_all(
            "CT Future Order",
            filters={"user": user},
            fields=["name", "symbol", "entry_price", "target_price",
                    "stop_loss", "creation"],
            limit=50,
            order_by="creation desc",
        )
        snap["future_orders"]       = fo
        snap["future_orders_count"] = len(fo)
    except Exception:
        pass

    # Market ticker for tracked symbols only (smaller payload)
    try:
        from coin_trader.exchange import get_active_exchange
        active_exchange = get_active_exchange(user)
    except Exception:
        active_exchange = "CoinDCX"

    if active_exchange == "Binance" and snap["tracked_symbols"]:
        try:
            from coin_trader.exchange_binance import get_ticker as bnb_ticker, _get_usdt_inr_rate
            bnb_data = bnb_ticker() or []
            inr_rate = _get_usdt_inr_rate()
            bnb_map  = {t.get("symbol", ""): t for t in bnb_data}
            for sym in snap["tracked_symbols"]:
                s = sym.strip().upper()
                bnb_sym = s[:-3] + "USDT" if s.endswith("INR") else s
                t = bnb_map.get(bnb_sym)
                if t:
                    last_usdt = flt(t.get("lastPrice", 0))
                    is_inr = s.endswith("INR")
                    snap["market"][s] = {
                        "last_price":     round(last_usdt * inr_rate, 4) if is_inr else last_usdt,
                        "high":           round(flt(t.get("highPrice", 0)) * inr_rate, 4) if is_inr else flt(t.get("highPrice", 0)),
                        "low":            round(flt(t.get("lowPrice", 0)) * inr_rate, 4) if is_inr else flt(t.get("lowPrice", 0)),
                        "volume":         flt(t.get("volume", 0)),
                        "bid":            round(flt(t.get("bidPrice", 0)) * inr_rate, 4) if is_inr else flt(t.get("bidPrice", 0)),
                        "ask":            round(flt(t.get("askPrice", 0)) * inr_rate, 4) if is_inr else flt(t.get("askPrice", 0)),
                        "change_24_hour": flt(t.get("priceChangePercent", 0)),
                        "timestamp":      None,
                    }
        except Exception as e:
            frappe.log_error(title="snapshot: Binance ticker", message=str(e))

    elif active_exchange == "WazirX" and snap["tracked_symbols"]:
        try:
            from coin_trader.exchange_wazirx import get_ticker as wzx_ticker
            wzx_data = wzx_ticker() or []
            # WazirX uses lowercase symbols; map by uppercase key
            wzx_map = {(t.get("symbol") or "").upper(): t for t in wzx_data}
            for sym in snap["tracked_symbols"]:
                s = sym.strip().upper()
                t = wzx_map.get(s)
                if t:
                    open_p = flt(t.get("openPrice", 0))
                    last_p = flt(t.get("lastPrice", 0))
                    snap["market"][s] = {
                        "last_price":     last_p,
                        "high":           flt(t.get("highPrice", 0)),
                        "low":            flt(t.get("lowPrice", 0)),
                        "volume":         flt(t.get("volume", 0)),
                        "bid":            flt(t.get("bidPrice", 0)),
                        "ask":            flt(t.get("askPrice", 0)),
                        "change_24_hour": round((last_p - open_p) / (open_p or 1) * 100, 3),
                        "timestamp":      None,
                    }
        except Exception as e:
            frappe.log_error(title="snapshot: WazirX ticker", message=str(e))

    else:
        try:
            ticker_data  = _fetch_ticker_cached() or []
            tracked_keys = {_norm_market_key(s) for s in snap["tracked_symbols"]}
            for t in ticker_data:
                mkt = t.get("market")
                if mkt in tracked_keys:
                    snap["market"][mkt] = {
                        "last_price":     flt(t.get("last_price", 0)),
                        "high":           flt(t.get("high", 0)),
                        "low":            flt(t.get("low", 0)),
                        "volume":         flt(t.get("volume", 0)),
                        "bid":            flt(t.get("bid", 0)),
                        "ask":            flt(t.get("ask", 0)),
                        "change_24_hour": flt(t.get("change_24_hour", 0)),
                        "timestamp":      t.get("timestamp"),
                    }
        except Exception:
            pass

    # Active AI provider
    try:
        ai = frappe.db.get_value("CT AI Provider", {"is_active": 1}, "provider")
        if ai:
            snap["ai_provider"] = ai
    except Exception:
        pass

    # Model trained?
    try:
        from coin_trader.ml.train_model import model_exists
        snap["model_trained"] = bool(model_exists(user))
    except Exception:
        pass

    return snap

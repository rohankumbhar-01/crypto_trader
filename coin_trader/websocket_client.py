"""
CoinDCX real-time feed via Socket.io 2.x (DcxStreams).

Public channels (no auth):
  join_channel  → "B-BTC_INR@ticker"
  join_channel  → "B-BTC_INR@depth"
  join_channel  → "B-BTC_INR@kline_1m"

Private channels (require auth token):
  join_channel  → "coindcx@balance"
  join_channel  → "coindcx@order"

Usage (blocking, run in a background thread / Frappe job):
    client = CoinDCXWebSocket(
        symbols=["B-BTC_INR", "B-ETH_INR"],
        on_candle=my_candle_handler,
        on_ticker=my_ticker_handler,
        interval="1m",
    )
    client.start()   # blocks
    client.stop()    # call from another thread
"""

import hashlib
import hmac
import json
import threading
import time

import frappe

_SOCKET_URL = "https://stream.coindcx.com"
_RECONNECT_DELAY = 5  # seconds between reconnect attempts
_MAX_RECONNECTS = 20


class CoinDCXWebSocket:
	def __init__(
		self,
		symbols: list[str],
		interval: str = "1m",
		on_candle=None,
		on_ticker=None,
		on_depth=None,
		on_balance=None,
		on_order=None,
		user: str | None = None,
	):
		"""
		symbols:    list of CoinDCX pair strings, e.g. ["B-BTC_INR", "B-ETH_INR"]
		interval:   candle interval for kline channel
		on_candle:  callback(symbol, candle_dict)
		on_ticker:  callback(symbol, ticker_dict)
		on_depth:   callback(symbol, depth_dict)
		on_balance: callback(balance_list)   — requires user
		on_order:   callback(order_dict)     — requires user
		user:       Frappe user for private channels
		"""
		self.symbols = symbols
		self.interval = interval
		self.on_candle = on_candle
		self.on_ticker = on_ticker
		self.on_depth = on_depth
		self.on_balance = on_balance
		self.on_order = on_order
		self.user = user

		self._sio = None
		self._stop_event = threading.Event()
		self._reconnects = 0

	# ------------------------------------------------------------------
	# Public interface
	# ------------------------------------------------------------------

	def start(self):
		"""Connect and block until stop() is called or max reconnects exceeded."""
		while not self._stop_event.is_set() and self._reconnects <= _MAX_RECONNECTS:
			try:
				self._connect()
			except Exception as e:
				frappe.log_error(title="CoinDCX WebSocket Error", message=str(e))
				self._reconnects += 1
				if not self._stop_event.is_set():
					time.sleep(_RECONNECT_DELAY)

	def stop(self):
		"""Signal the client to disconnect and stop reconnecting."""
		self._stop_event.set()
		if self._sio:
			try:
				self._sio.disconnect()
			except Exception:
				pass

	# ------------------------------------------------------------------
	# Internal
	# ------------------------------------------------------------------

	def _connect(self):
		try:
			import socketio
		except ImportError:
			frappe.throw(
				"python-socketio is required for WebSocket feeds. "
				"Run: pip install 'python-socketio[client]'"
			)

		sio = socketio.Client(
			reconnection=False,
			logger=False,
			engineio_logger=False,
		)
		self._sio = sio

		@sio.event
		def connect():
			self._reconnects = 0
			self._subscribe(sio)

		@sio.event
		def disconnect():
			pass

		@sio.on("candle")
		def on_candle(data):
			if self.on_candle:
				try:
					symbol = data.get("s", "")
					self.on_candle(symbol, data)
				except Exception as e:
					frappe.log_error(title="WebSocket candle handler error", message=str(e))

		@sio.on("ticker")
		def on_ticker(data):
			if self.on_ticker:
				try:
					symbol = data.get("market", data.get("s", ""))
					self.on_ticker(symbol, data)
				except Exception as e:
					frappe.log_error(title="WebSocket ticker handler error", message=str(e))

		@sio.on("depth")
		def on_depth(data):
			if self.on_depth:
				try:
					symbol = data.get("s", "")
					self.on_depth(symbol, data)
				except Exception as e:
					frappe.log_error(title="WebSocket depth handler error", message=str(e))

		@sio.on("balance-update")
		def on_balance(data):
			if self.on_balance:
				try:
					self.on_balance(data)
				except Exception as e:
					frappe.log_error(title="WebSocket balance handler error", message=str(e))

		@sio.on("order-update")
		def on_order(data):
			if self.on_order:
				try:
					self.on_order(data)
				except Exception as e:
					frappe.log_error(title="WebSocket order handler error", message=str(e))

		sio.connect(
			_SOCKET_URL,
			transports=["websocket"],
			socketio_path="/socket.io",
		)
		sio.wait()

	def _subscribe(self, sio):
		"""Emit join_channel for all public and private channels."""
		channels = []

		for symbol in self.symbols:
			channels.append(f"{symbol}@ticker")
			channels.append(f"{symbol}@kline_{self.interval}")
			channels.append(f"{symbol}@depth")

		for ch in channels:
			sio.emit("join_channel", {"channelName": ch})

		if self.user and (self.on_balance or self.on_order):
			token = _get_auth_token(self.user)
			if token:
				sio.emit("join_channel", {"channelName": "coindcx@balance", "authToken": token})
				sio.emit("join_channel", {"channelName": "coindcx@order", "authToken": token})


# ---------------------------------------------------------------------------
# Auth token for private WebSocket channels
# ---------------------------------------------------------------------------

def _get_auth_token(user: str) -> str | None:
	"""
	Generate a short-lived auth token for private Socket.io channels.
	CoinDCX expects HMAC-SHA256(timestamp_ms, api_secret).
	"""
	try:
		from frappe.utils.password import get_decrypted_password

		row = frappe.db.get_value(
			"CT Exchange Credential",
			{"user": user, "is_active": 1},
			["name", "api_key", "api_secret"],
			as_dict=True,
		)
		if not row:
			return None
		api_secret = get_decrypted_password(
			"CT Exchange Credential", row["name"], "api_secret"
		)
		ts = str(int(time.time() * 1000))
		sig = hmac.new(
			api_secret.encode("utf-8"),
			ts.encode("utf-8"),
			hashlib.sha256,
		).hexdigest()
		return json.dumps({"key": row["api_key"], "timestamp": ts, "signature": sig})
	except Exception as e:
		frappe.log_error(title="WebSocket auth token error", message=str(e))
		return None


# ---------------------------------------------------------------------------
# Frappe-managed singleton feed
# ---------------------------------------------------------------------------

_feed_thread: threading.Thread | None = None
_feed_client: CoinDCXWebSocket | None = None
_feed_lock = threading.Lock()


def start_market_feed(
	symbols: list[str],
	interval: str = "1m",
	on_candle=None,
	on_ticker=None,
	user: str | None = None,
) -> None:
	"""
	Start the market feed in a daemon thread.
	Only one feed runs at a time per process — call stop_market_feed() first to restart.
	"""
	global _feed_thread, _feed_client

	with _feed_lock:
		if _feed_thread and _feed_thread.is_alive():
			return

		client = CoinDCXWebSocket(
			symbols=symbols,
			interval=interval,
			on_candle=on_candle,
			on_ticker=on_ticker,
			user=user,
		)
		_feed_client = client

		t = threading.Thread(target=client.start, daemon=True, name="coindcx-ws-feed")
		_feed_thread = t
		t.start()


def stop_market_feed() -> None:
	"""Stop the running market feed thread."""
	global _feed_client
	with _feed_lock:
		if _feed_client:
			_feed_client.stop()
			_feed_client = None

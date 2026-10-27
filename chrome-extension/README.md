# Coin Trader Chrome Extension

A Manifest V3 Chrome control panel for the existing Frappe Coin Trader app. It reads the authenticated user's trading configuration, recent trade log, open positions, and summary stats through server-side whitelisted endpoints.

## Current capabilities

- Connection settings for a self-hosted Frappe site.
- Dashboard snapshot and recent trade history.
- Displays whether the server config is in paper or live mode.
- **Force paper mode** action; the extension never enables live trading.
- **Run paper scan** action, guarded server-side so it refuses non-paper configurations.
- Exchange credentials remain on the server; the extension only stores a Frappe API token locally in Chrome storage.

## Install the backend endpoint

Copy `coin_trader/chrome_extension_api.py` into the installed app's `coin_trader/` package and restart/reload Frappe:

```bash
bench --site your-site.example migrate
bench restart
```

The endpoint path is `/api/method/coin_trader.chrome_extension_api.<method>`.

The API uses the authenticated Frappe user. Use a dedicated Frappe user/API key with access only to the relevant Coin Trader records. Do not use Administrator credentials. API key format in extension settings is `api_key:api_secret`.

## Load the extension

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Select **Load unpacked** and choose this `chrome-extension/` directory.
4. Open the extension's **Details → Extension options**.
5. Enter your Frappe HTTPS site URL and dedicated API token.
6. Save and test the connection, then open the extension popup.

For a local development site, `http://localhost` and `http://127.0.0.1` are accepted. Non-local HTTP URLs are rejected.

## Safety notes

- Start in paper mode and verify the backend reports the correct config.
- The extension does not provide a live-trading enable button.
- The scan endpoint checks `dry_run` server-side immediately before invoking the scanner. Confirm the existing Coin Trader scanner and exchange adapter behave as expected in your installed version before using it.
- Do not expose Frappe API credentials in screenshots, logs, or Git.
- Chrome local extension storage is not a secrets vault. Use a dedicated API key, restrict that user's permissions, and revoke the key if the browser profile is compromised.

## Development notes

No build step or third-party frontend dependencies are required. The popup uses vanilla HTML, CSS, and JavaScript.

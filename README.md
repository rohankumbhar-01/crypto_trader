# Coin Trader

**AI + ML Powered CoinDCX Crypto Trading Platform for Frappe v16 / ERPNext**

[![Frappe v16](https://img.shields.io/badge/Frappe-v16-orange)](https://frappe.io)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## Overview

Coin Trader is a full-featured algorithmic crypto trading app built on the Frappe framework. It integrates directly with the **CoinDCX** exchange and combines a custom Machine Learning pipeline with AI-powered signal validation (Claude / OpenAI / Gemini) to automate trade execution with configurable risk management.

```
Market Data (CoinDCX WebSocket + REST)
        ↓
 Indicator Engine  (EMA, RSI, ATR, VWAP, Bollinger Bands)
        ↓
 ML Probability Engine  (Random Forest — walk-forward trained)
        ↓
 AI Validation Layer  (Claude / OpenAI / Gemini)
        ↓
 Risk Engine  (Kelly-lite sizing, drawdown guard, cooldown)
        ↓
 Trade Executor  (live + paper trading)
        ↓
 Position Monitor  (trailing SL, target exit, real-time PnL)
```

---

## Features

| Category | Details |
|---|---|
| **Exchange** | CoinDCX REST + WebSocket (live order book, real-time prices) |
| **ML Pipeline** | Walk-forward training, 30+ technical indicator features, Random Forest |
| **AI Validation** | Claude, OpenAI, Gemini — configurable per signal |
| **Risk Engine** | Kelly-lite position sizing, daily loss guard, position cap, cooldown, trailing stoploss |
| **Trading Modes** | Paper trading (dry run) + Live trading |
| **Backtesting** | Walk-forward backtest with Sharpe ratio, win rate, profit factor, max drawdown |
| **Dashboard** | Real-time dark-theme web dashboard at `/ct-dashboard` |
| **Automation** | Frappe scheduler — scan every 15 min, retrain nightly, daily P&L summary |
| **Notifications** | Telegram + email alerts on trade open/close |

---

## Requirements

- Frappe v16
- Python 3.11+
- MariaDB 10.6+
- CoinDCX account with API credentials
- *(Optional)* Claude / OpenAI / Gemini API key for AI signal validation

---

## Installation

```bash
# From your bench directory
bench get-app https://github.com/rohankumbhar-01/crypto_trader.git

bench --site your-site.com install-app coin_trader

bench --site your-site.com migrate

bench build --app coin_trader
```

---

## Setup Guide

### 1. Exchange Credentials

Go to **Coin Trader → Configuration → CT Exchange Credential** and create a record:

| Field | Value |
|---|---|
| Exchange | CoinDCX |
| API Key | Your CoinDCX API key |
| API Secret | Your CoinDCX API secret |
| User | Your Frappe user |

> Keys are stored using Frappe's encrypted `password` field type.

---

### 2. Trading Configuration

Go to **Coin Trader → Configuration → CT Trading Config** and create a config:

| Field | Description | Example |
|---|---|---|
| User | Frappe user this config belongs to | `Administrator` |
| Is Active | Enable this config | ✓ |
| Dry Run | Paper trading mode — no real orders placed | ✓ *(start here)* |
| Candle Interval | OHLCV timeframe | `1h` |
| INR Per Trade | Fixed capital per trade in INR | `5000` |
| Max Positions | Max concurrent open positions | `5` |
| Stop Loss % | Risk % of capital per trade (Kelly input) | `1.5` |
| Max Daily Loss INR | Halt trading if daily loss exceeds this | `2000` |
| Min Confidence % | Minimum ML confidence required to trade | `80` |
| Cooldown After Loss (min) | Minimum gap after a losing trade | `30` |

Add symbols in the **Trading Symbols** child table (e.g. `BTCINR`, `ETHINR`, `SOLINR`) and set **Enabled = 1** for each.

---

### 3. AI Provider *(optional)*

Go to **Coin Trader → ML & AI → CT AI Provider**:

| Field | Value |
|---|---|
| Provider | `claude` / `openai` / `gemini` |
| API Key | Your provider API key |
| Model | e.g. `claude-sonnet-4-6` |
| Is Active | ✓ |

If no active AI provider is configured, the system trades on the ML signal alone.

---

### 4. Train the ML Model

The model trains automatically at **2 AM daily**. To train manually:

```bash
bench --site your-site.com execute coin_trader.ml.train_model.daily_train
```

The dashboard header shows a **"MODEL NOT TRAINED"** warning badge until the first training run completes.

---

## Dashboard

Navigate to **`/ct-dashboard`** after logging in.

### Header
| Element | Description |
|---|---|
| PAPER TRADING badge | Shown when Dry Run is active |
| LIVE badge | Shown when trading with real funds |
| Open Positions pill | Live count of currently open trades |
| Today PnL pill | Running realised P&L for today in INR |
| Run Scan button | Manually trigger a full symbol scan |

### Toolbar
| Control | Action |
|---|---|
| Symbol selector | Pick a crypto pair |
| Interval selector | `1m` / `5m` / `15m` / `30m` / `1h` / `4h` / `1d` |
| **Get Signal** | Fetch ML + AI signal for selected symbol |
| **Run Scan** | Scan all configured symbols and execute approved trades |
| **Run Backtest** | Walk-forward backtest on the selected symbol |

### Panels
| Panel | Description |
|---|---|
| ML Signal | BUY/SELL/HOLD prediction, confidence bar, SL/target levels, AI reasoning |
| Open Positions | Live table — buy price, current SL, target, PnL, close button |
| Latest Scan Results | Full scan output with risk engine approve/reject reasons |
| Recent Trades | Last 50 trade log entries with action, price, PnL |
| Backtest Result | Metrics after running a backtest |

---

## Scheduler Jobs

| Schedule | Job | Description |
|---|---|---|
| Every 15 min | `run_scanner` | Scan all symbols, execute approved trades |
| Every tick | `monitor_positions` | Check SL/target, update trailing stoploss |
| Daily 2 AM | `daily_ml_retrain` | Retrain ML model on fresh historical data |
| Daily | `generate_daily_summary` | Write CT Daily Summary with P&L stats |

---

## Risk Management

The Risk Engine applies these checks **in order** before placing any trade:

1. **Confidence gate** — rejects if ML confidence < `min_confidence_pct`
2. **Daily loss guard** — halts if today's realized loss ≥ `max_daily_loss_inr`
3. **Max open positions** — rejects if open position count ≥ `max_positions`
4. **Duplicate guard** — rejects if an open position already exists for the symbol
5. **Cooldown** — rejects if the symbol was traded within `cooldown_after_loss_min` minutes
6. **Balance check** — rejects if available INR < `inr_per_trade`
7. **Position sizing** — `qty = (balance × risk_pct%) / |price − stoploss|`, capped at `inr_per_trade`

---

## Paper Trading vs Live Trading

| Setting | Dry Run = 1 (Paper) | Dry Run = 0 (Live) |
|---|---|---|
| ML + AI signal | ✓ | ✓ |
| Risk engine | ✓ | ✓ |
| Order placement | Simulated | Real (CoinDCX API) |
| P&L | Simulated | Real |
| Dashboard badge | PAPER TRADING | LIVE |

**Switch to live only after:**
- Model shows ≥ 60% backtest win rate
- Signals validated in paper mode for at least 1 week
- Daily loss limits are set conservatively

---

## Backtesting

Walk-forward methodology:
- Fetches up to 1000 historical candles from CoinDCX
- Applies the same indicator + ML pipeline used in live trading
- Simulates one position at a time (no overlapping trades)
- Exits on stoploss hit, target hit, or 20-candle timeout
- 200-candle warmup period before first signal is generated

**Output metrics:** Win Rate · Profit Factor · Net Profit % · Max Drawdown % · Sharpe Ratio · Avg Win % · Avg Loss % · Avg Duration · Expectancy %

Results saved to **CT Backtest Result** and displayed in the dashboard.

---

## DocTypes Reference

| DocType | Purpose |
|---|---|
| CT Trading Config | Per-user trading parameters + symbol list |
| CT Exchange Credential | Encrypted CoinDCX API keys |
| CT Open Position | Currently open trade positions |
| CT Trade Log | Full trade history (entry + exit) |
| CT AI Scan Result | Per-symbol ML + AI scan output |
| CT ML Prediction | Stored ML prediction records |
| CT AI Provider | AI model config (Claude / OpenAI / Gemini) |
| CT Backtest Result | Walk-forward backtest metrics |
| CT Daily Summary | Daily P&L and win/loss statistics |
| CT Notification Config | Telegram / email alert settings |
| CT Future Order | Scheduled future order queue |

---

## Project Structure

```
coin_trader/
├── ai_adapter.py              # AI provider abstraction (Claude/OpenAI/Gemini)
├── backtest_engine.py         # Backtest API + result persistence
├── exchange.py                # CoinDCX REST API client
├── hooks.py                   # Frappe hooks, scheduler, fixtures
├── market_data.py             # OHLCV fetcher + indicator computation
├── probability_engine.py      # ML signal scoring engine
├── risk_engine.py             # Risk checks + position sizing (Kelly-lite)
├── scanner.py                 # Multi-symbol scan orchestration
├── startup.py                 # Boot session handler
├── tasks.py                   # Scheduled task entry points
├── trader.py                  # Order execution + position monitor
├── websocket_client.py        # CoinDCX WebSocket price feed
├── ml/
│   ├── backtesting.py         # Walk-forward backtest core logic
│   ├── dataset_builder.py     # Training dataset construction
│   ├── feature_engineering.py # Technical indicator feature set
│   ├── predict.py             # Model inference wrapper
│   └── train_model.py         # Model training + disk persistence
├── crypto_trader/
│   └── doctype/               # 11 DocType definitions (JSON + Python)
├── desktop_icon/
│   └── coin_trader.json       # Frappe desk home icon
├── workspace_sidebar/
│   └── coin_trader.json       # Frappe sidebar navigation
├── public/
│   ├── css/ct_dashboard.css   # Dark-theme dashboard styles
│   ├── js/ct_dashboard.js     # Dashboard JS (signals, positions, scan)
│   └── images/coin_trader_logo.svg
└── www/
    ├── ct-dashboard.html      # Jinja template
    └── ct_dashboard.py        # Context loader (get_context)
```

---

## License

MIT — see [LICENSE](LICENSE)

---

## Author

**Rohan Kumbhar** · [Dexciss Technology](https://dexciss.com)  
Email: rkrohankumbhar@gmail.com

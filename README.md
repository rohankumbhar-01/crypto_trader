<div align="center">

<img src="coin_trader/public/images/coin_trader_logo.svg" width="120" alt="Coin Trader Logo"/>

# 🪙 Coin Trader

### Frappe's First AI + ML Powered Crypto Trading Platform

[![Frappe](https://img.shields.io/badge/Built%20on-Frappe%20v16-1f6feb?style=for-the-badge&logo=frappe)](https://frappeframework.com)
[![Python](https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![LightGBM](https://img.shields.io/badge/ML-LightGBM-00b4d8?style=for-the-badge)](https://lightgbm.readthedocs.io)
[![Claude AI](https://img.shields.io/badge/AI-Claude%20(Anthropic)-f5a623?style=for-the-badge)](https://anthropic.com)
[![CoinDCX](https://img.shields.io/badge/Exchange-CoinDCX-6c47ff?style=for-the-badge)](https://coindcx.com)
[![License](https://img.shields.io/badge/License-MIT-10d57b?style=for-the-badge)](LICENSE)

**A fully automated, real-time cryptocurrency trading system built entirely on the Frappe framework.**
Combines Machine Learning predictions, Claude AI validation, and a live fullscreen dashboard —
all in one ERPNext-grade application.

[🚀 Features](#-features) · [📊 Dashboard](#-live-dashboard) · [🤖 How It Works](#-how-it-works) · [⚙️ Installation](#️-installation)

---

</div>

## 🌟 Why Coin Trader?

> **This is the first Frappe application in the world that combines blockchain trading, Machine Learning, and Generative AI into a single deployable ERPNext module.**

Built from scratch by a single developer — Rohan Kumbhar — Coin Trader reimagines what's possible on the Frappe framework, taking it far beyond ERP into real-time financial automation.

| What Makes It Unique | Details |
|---|---|
| 🧠 **Dual AI Engine** | LightGBM ML model + Claude AI working in tandem for every trade decision |
| 📡 **Real-Time Everything** | Live market data, live P&L, live order book — zero manual refresh |
| 🔒 **Risk-First Design** | 5-layer risk guard before any trade executes |
| 📄 **Paper + Live Mode** | Test strategies safely in paper mode before going live with real funds |
| 🏗️ **Pure Frappe** | No external dashboard — runs natively inside your Frappe/ERPNext instance |

---

## 🚀 Features

### 📊 Live Fullscreen Dashboard
- Real-time candlestick price chart powered by TradingView Lightweight Charts
- Live order book with bid/ask depth bars and spread calculation
- Scrolling ticker strip with 24h price change for all tracked coins
- 6-KPI row: INR Balance · Today P&L · Open Positions · Pending Orders · Unrealised P&L · AI Engine
- Market overview panel with volume, 24h high/low, and flash animations on price updates
- **Auto-refreshes every 5 seconds** — completely hands-free

### 🤖 Machine Learning Engine
- **LightGBM** gradient boosting model trained daily on real OHLCV candle data
- **20+ technical indicators**: RSI, MACD, Bollinger Bands, ATR, EMA cross, volume ratios, momentum
- Walk-forward backtesting with realistic trade simulation and metrics
- Per-user model — each user trains and owns their own personalized model
- Configurable confidence threshold (default 80%) — only high-conviction signals trade

### 🧠 Claude AI Validation Layer
- Every ML signal is independently validated by Claude (Anthropic)
- AI cross-checks technical patterns against broader market context
- Returns human-readable reasons: *"RSI oversold at 28, volume spike 3x average — BUY confirmed"*
- Pluggable: switch between Claude, GPT-4, or disable AI layer entirely

### 🛡️ 5-Layer Risk Engine
1. **Daily loss limit** — halts all trading if the configured INR loss is breached
2. **Max open positions** — never over-allocates capital across multiple coins
3. **Cooldown after loss** — enforces a waiting period to prevent revenge trading
4. **Consecutive loss guard** — pauses trading after N losses in a row
5. **Trailing stop-loss** — dynamically moves stop-loss up as price rises to lock in profits

### 📡 CoinDCX Integration
- Live market ticker for all INR pairs (BTCINR, ETHINR, SOLINR, etc.)
- Real-time order book (bid/ask depth) per symbol
- OHLCV candle data for all timeframes: 5m, 15m, 1h, 4h, 1d
- Place live market buy/sell orders with quantity calculation
- Portfolio INR balance sync
- All API calls proxied server-side — API keys never exposed to the browser

### 🔔 Smart Notification System
- **Telegram Bot** — rich HTML-formatted alerts delivered instantly
- **Email** — via Frappe's built-in email system
- Alert events:
  - 🟢 Position opened (symbol, price, qty, target, stop-loss)
  - 🛑 Stop-loss hit (exit price, loss amount)
  - 🎯 Target reached (exit price, profit)
  - 🔎 Scan complete (BUY/SELL signals with confidence %)
  - 📊 Daily P&L summary (total P&L, win rate, open positions)
- **Test button** directly on the config form — verify your Telegram/Email in one click

### 📜 Complete Trade Lifecycle (Fully Automated)
```
Scheduler (every 15 min)
  → ML Predict → AI Validate → Risk Check → Execute Order
  → Monitor Position (every 1 min) → Close on SL/Target
  → Log Trade → Send Notification → Update Dashboard
```

### 🧪 Walk-Forward Backtesting
- Run realistic backtests on up to 1000 historical candles
- Metrics: Win Rate, Profit Factor, Sharpe Ratio, Max Drawdown, Expectancy, Avg Hold
- Per-trade breakdown with entry, exit, duration, and P&L
- Directly accessible from the AI Signal Probe panel on the live dashboard

---

## 📊 Live Dashboard

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  🪙 Coin Trader   AI + ML Trading Platform    ● PAPER   LIVE · 10:50:01 am │
├──────────┬──────────┬──────────┬──────────┬────────────┬───────────────────┤
│ INR Bal  │ Today P&L│ Open Pos │ Pending  │ Unrealised │ AI Engine         │
│ ₹1,007   │   +₹0    │    0     │    0     │    +₹0     │ Claude · 5 coins  │
├──────────┴──────────┴──────────┴──────────┴────────────┴───────────────────┤
│  BTCINR ₹77,48,549 ▼1.15%  ETHINR ₹2,19,850 ▼1.29%  DOGEINR ₹11 ▲1.41%  │
├──────────────────┬─────────────────────────────┬──────────────────────────┤
│  📈 Market       │  📊 Price Chart  [ETHINR ▾] │  📒 Order Book  ETHINR  │
│  BTCINR  ₹77.5L  │  ┌───────────────────────┐  │  BID       QTY  ASK QTY │
│  ETHINR  ₹2.2L   │  │  🕯🕯📈🕯🕯🕯🕯🕯🕯🕯    │  │  2,18,697      2,19,670 │
│  SOLINR  ₹8,837  │  │   candlestick chart   │  │  2,18,256      2,20,219 │
│  DOGEINR ₹11     │  │   (1h · 500 candles)  │  │  Spread 0.445%          │
│  SHIBINR ₹0.0006 │  └───────────────────────┘  │                         │
├──────────────────┴─────────────────────────────┼──────────────────────────┤
│  💼 Open Positions                 0 open       │  🤖 AI Signal Probe      │
│  No open positions                             │  HOLD · 60% confidence   │
├────────────────────────────────────────────────┴──────────────────────────┤
│  ⏳ Future Buy Orders  ·  🔎 Latest Scan Results  ·  📜 Recent Trades      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🤖 How It Works

```
Every 15 minutes (Frappe Scheduler):

  CoinDCX API ──► Candle Data (OHLCV, 1000 bars)
                        │
                        ▼
              ┌──────────────────┐
              │  Feature Engine  │  RSI, MACD, BB, ATR, EMA, Volume...
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │  LightGBM Model  │  Trained daily · Per-user
              │  (ML Predict)    │  Outputs: BUY/SELL/HOLD + confidence %
              └────────┬─────────┘
                       │  confidence >= 80%?
                       ▼
              ┌──────────────────┐
              │   Claude AI      │  Independent cross-validation
              │   Validation     │  Human-readable reasoning
              └────────┬─────────┘
                       │  approved?
                       ▼
              ┌──────────────────┐
              │   Risk Engine    │  5 guards: loss limit, position cap,
              │   (5 layers)     │  cooldown, SL, trailing SL
              └────────┬─────────┘
                       │  go?
                       ▼
              ┌──────────────────┐
              │  Trade Executor  │  Paper (simulated) or Live (real order)
              └────────┬─────────┘
                       │
                       ▼
         Position opened → Monitor every 1 min → Close on SL/Target
                       │
                       ▼
         CT Trade Log + Telegram/Email Notification + Dashboard update
```

---

## 📦 Frappe DocTypes (10 Custom DocTypes)

| DocType | Purpose |
|---|---|
| `CT Trading Config` | Per-user trading configuration — symbols, risk params, AI settings |
| `CT Trading Symbol` | Child table: list of tracked coin symbols per config |
| `CT Open Position` | Currently open trades with real-time live P&L tracking |
| `CT Trade Log` | Complete immutable history of every trade (buy + sell) |
| `CT Future Order` | Pending dip-entry orders waiting for price to drop |
| `CT Backtest Result` | Walk-forward backtest results with full metrics |
| `CT Daily Summary` | End-of-day aggregated P&L and trade count |
| `CT AI Provider` | Claude / GPT-4 API key and model configuration |
| `CT Exchange Credential` | CoinDCX API key + secret (encrypted via Frappe Password field) |
| `CT Notification Config` | Telegram bot token + chat ID + email for alerts |

---

## ⚙️ Installation

### Prerequisites
- Frappe v16 bench (`bench` CLI)
- Python 3.11+
- MariaDB / MySQL
- Redis

### Steps

```bash
# 1. Get the app
bench get-app https://github.com/rohankumbhar-01/crypto_trader.git

# 2. Install on your site
bench --site your-site.com install-app coin_trader

# 3. Run database migrations
bench --site your-site.com migrate

# 4. Build frontend assets
bench build --app coin_trader

# 5. Restart
bench restart
```

### First-Time Setup (5 minutes)

```
1. Exchange Credential  →  /desk#Form/CT Exchange Credential/new
                           Add your CoinDCX API Key + Secret

2. AI Provider          →  /desk#Form/CT AI Provider/new
                           Add your Anthropic (Claude) API key

3. Trading Config       →  /desk#Form/CT Trading Config/new
                           Add symbols: BTCINR, ETHINR, SOLINR...
                           Set risk params. Enable paper mode first!

4. Train Model          →  Click "Train Model" on CT Trading Config
                           (takes ~30 seconds)

5. Open Dashboard       →  /ct-dashboard
                           🎉 You're live!

6. Notifications        →  /desk#Form/CT Notification Config/new
                           Add Telegram bot token + chat ID
                           Click "Send Test Notification" to verify
```

---

## 🔧 Key Configuration Parameters

```python
# CT Trading Config
candle_interval    = "15m"   # Candle timeframe: 5m, 15m, 1h, 4h, 1d
min_confidence_pct = 80      # Min ML confidence % to consider trading
inr_per_trade      = 500     # Capital allocated per trade (INR)
max_positions      = 2       # Maximum simultaneous open positions
stop_loss_pct      = 1.0     # Stop-loss trigger (% below entry)
profit_target_pct  = 2.0     # Take-profit trigger (% above entry)
max_daily_loss_inr = 1000    # Daily loss circuit breaker (INR)
cooldown_min       = 30      # Cooldown minutes after a losing trade
dry_run            = True    # Paper mode — no real orders placed
```

---

## 📁 Project Structure

```
coin_trader/
├── dashboard_api.py          # CoinDCX server-side proxy + snapshot API
├── scanner.py                # Scheduled scan orchestrator (every 15 min)
├── trader.py                 # Trade execution engine (paper + live)
├── notifier.py               # Telegram + Email notification system
├── exchange.py               # CoinDCX REST API wrapper
├── risk_engine.py            # 5-layer risk guard system
├── ai_adapter.py             # Claude / GPT-4 AI validation layer
├── backtest_engine.py        # Walk-forward backtesting engine
├── tasks.py                  # Frappe scheduler entry points
├── hooks.py                  # Frappe app hooks and scheduler config
├── ml/
│   ├── features.py           # 20+ technical indicator feature builder
│   ├── train_model.py        # LightGBM daily training pipeline
│   └── predict.py            # Model inference + confidence scoring
├── public/
│   ├── js/ct_dashboard.js    # 750-line real-time dashboard controller
│   └── css/ct_dashboard.css  # Dark-theme dashboard stylesheet
├── www/
│   └── ct-dashboard.html     # Fullscreen dashboard Jinja template
└── crypto_trader/doctype/    # 10 custom Frappe DocTypes
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Framework** | Frappe v16 (Python backend + JS frontend) |
| **ML Model** | LightGBM (gradient boosting decision trees) |
| **AI Validation** | Claude 3.5 Sonnet (Anthropic API) |
| **Exchange API** | CoinDCX (India's leading crypto exchange) |
| **Charting** | TradingView Lightweight Charts v4.1 |
| **Realtime** | Frappe WebSocket / Socket.IO |
| **Database** | MariaDB via Frappe ORM |
| **Scheduler** | Frappe Scheduler (cron-based) |
| **Notifications** | Telegram Bot API + Frappe Sendmail |

---

## 🗺️ Roadmap

- [ ] Multi-exchange support (Binance, WazirX)
- [ ] Portfolio rebalancing automation
- [ ] No-code strategy builder UI
- [ ] Webhook-based instant signal alerts
- [ ] Mobile PWA dashboard
- [ ] Reinforcement learning trader (PPO agent)

---

## 👨‍💻 Author

<div align="center">

**Built entirely by Rohan Kumbhar**

*"I built Frappe's first AI + ML crypto trading platform — from scratch, completely alone."*

This project has no organizational backing. Every line of code — from the ML pipeline to the real-time dashboard to the risk engine — was designed and written by a single developer with a vision to prove that Frappe can power anything.

[![GitHub](https://img.shields.io/badge/GitHub-rohankumbhar--01-181717?style=for-the-badge&logo=github)](https://github.com/rohankumbhar-01)

</div>

---

## ⚠️ Disclaimer

This software is for **educational and research purposes only**.
Cryptocurrency trading involves substantial financial risk and you may lose your entire investment.
Always test extensively in **paper mode** before using real funds.
The author is not responsible for any trading losses incurred using this software.

---

<div align="center">

**⭐ If this project impressed you, give it a star!**

*Coin Trader — Where Frappe meets the Blockchain*

</div>

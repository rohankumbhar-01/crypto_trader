/**
 * Coin Trader Ultra Dashboard
 * Real-time CoinDCX market data + AI/ML trading signals
 */

"use strict";

/* ══════════════════════════════════════════════════════════════════════
   GLOBALS
══════════════════════════════════════════════════════════════════════ */
const CT = window._CT || { symbols: [], interval: "1h", min_conf: 80, dry_run: true, max_positions: 5 };

const COINDCX_TICKER = "https://api.coindcx.com/exchange/ticker";
const COINDCX_OB     = "https://public.coindcx.com/market_data/orderbook?pair=";

let _tickerData   = {};      // { market: tickerObj }
let _tickerTimer  = null;
let _posTimer     = null;
let _futureTimer  = null;
let _obTimer      = null;
let _countTimer   = null;
let _countdown    = 30;
let _currentOBSym = null;
let _scanInFlight = false;

/* ══════════════════════════════════════════════════════════════════════
   BOOT
══════════════════════════════════════════════════════════════════════ */
frappe.ready(function () {
    initRealtime();
    startTickerFeed();
    loadPositions();
    loadTradeHistory();
    loadFutureOrders();
    startCountdown();

    // Refresh positions every 30s
    _posTimer = setInterval(loadPositions, 30_000);
    // Refresh future orders every 5s
    _futureTimer = setInterval(loadFutureOrders, 5_000);

    // Button wiring
    $("#btn-scan").on("click", runScan);
    $("#btn-probe").on("click", runProbe);
    $("#btn-backtest").on("click", runBacktest);
    $("#btn-refresh-pos").on("click", loadPositions);
    $("#btn-close-bt").on("click", () => { $("#card-backtest").addClass("hidden"); });

    // Symbol change → update orderbook
    $("#symbol-select").on("change", function () {
        _currentOBSym = this.value;
        loadOrderBook(_currentOBSym);
    });

    // Init orderbook for first symbol
    if (CT.symbols.length) {
        _currentOBSym = CT.symbols[0];
        loadOrderBook(_currentOBSym);
        _obTimer = setInterval(() => loadOrderBook(_currentOBSym), 8_000);
    }
});

/* ══════════════════════════════════════════════════════════════════════
   REALTIME (Frappe events)
══════════════════════════════════════════════════════════════════════ */
function initRealtime() {
    frappe.realtime.on("ct_scan_complete",    (d) => { renderScanResults(d.results || []); loadPositions(); updateLastScan(); });
    frappe.realtime.on("ct_position_opened",  ()  => { loadPositions(); updateKpiOpen(); frappe.show_alert({ message: "Position opened!", indicator: "green" }, 4); });
    frappe.realtime.on("ct_position_closed",  ()  => { loadPositions(); loadTradeHistory(); updateKpiOpen(); frappe.show_alert({ message: "Position closed", indicator: "blue" }, 4); });
}

/* ══════════════════════════════════════════════════════════════════════
   COUNTDOWN REFRESH INDICATOR
══════════════════════════════════════════════════════════════════════ */
function startCountdown() {
    _countdown = 30;
    updateCountdownUI();
    _countTimer = setInterval(() => {
        _countdown--;
        if (_countdown <= 0) {
            _countdown = 30;
            loadPositions();
        }
        updateCountdownUI();
    }, 1_000);
}

function updateCountdownUI() {
    $("#refresh-countdown").text(`Auto-refresh in ${_countdown}s`);
}

/* ══════════════════════════════════════════════════════════════════════
   TICKER FEED  (CoinDCX public /exchange/ticker)
══════════════════════════════════════════════════════════════════════ */
function startTickerFeed() {
    fetchTicker();
    _tickerTimer = setInterval(fetchTicker, 6_000);
}

async function fetchTicker() {
    try {
        const resp = await fetch(COINDCX_TICKER);
        if (!resp.ok) return;
        const data = await resp.json();
        data.forEach(t => { _tickerData[t.market] = t; });
        renderTickerBar();
        renderMarketOverview();
        updateKpiFromTicker();
    } catch (e) { /* silent — network hiccup */ }
}

function renderTickerBar() {
    const tracked = CT.symbols.map(s => normalizeMarket(s)).filter(Boolean);
    const items   = tracked.map(mkt => {
        const t   = _tickerData[mkt];
        if (!t) return "";
        const chg = parseFloat(t.change_24_hour || 0);
        const cls = chg >= 0 ? "ct-buy" : "ct-sell";
        const sign = chg >= 0 ? "▲" : "▼";
        return `<div class="ct-ticker-item">
          <span class="ct-ticker-symbol">${shortSym(mkt)}</span>
          <span class="ct-ticker-price ${cls}">₹${fmt(t.last_price)}</span>
          <span class="ct-ticker-chg ${cls}">${sign} ${Math.abs(chg).toFixed(2)}%</span>
        </div>`;
    }).join("");

    // Duplicate for seamless loop
    const content = items + items;
    const track   = document.getElementById("ticker-track");
    if (track && content.trim()) track.innerHTML = content;
}

function renderMarketOverview() {
    const tracked = CT.symbols.map(s => normalizeMarket(s)).filter(Boolean);
    if (!tracked.length) { $("#market-body").html('<div class="ct-empty-state">No symbols configured</div>'); return; }

    let html = '<div class="ct-market-grid">';
    tracked.forEach(mkt => {
        const t   = _tickerData[mkt];
        const sym = shortSym(mkt);
        if (!t) { html += `<div class="ct-market-row"><div class="ct-market-symbol">${sym}</div><div class="ct-muted-text">—</div><div></div><div></div></div>`; return; }
        const chg  = parseFloat(t.change_24_hour || 0);
        const cls  = chg >= 0 ? "ct-chg-up" : "ct-chg-down";
        const sign = chg >= 0 ? "▲" : "▼";
        const pcls = chg >= 0 ? "ct-buy" : "ct-sell";
        html += `<div class="ct-market-row" onclick="setSymbol('${mkt}')">
          <div>
            <div class="ct-market-symbol">${sym}</div>
            <div class="ct-market-name">${t.market || mkt}</div>
          </div>
          <div class="ct-market-price-wrap">
            <div class="ct-market-price ${pcls}">₹${fmt(t.last_price)}</div>
            <div class="ct-market-vol">Vol: ${fmtVol(t.volume)}</div>
          </div>
          <div><div class="ct-market-price ${pcls}" style="font-size:11px">H: ₹${fmt(t.high)}</div><div class="ct-market-vol">L: ₹${fmt(t.low)}</div></div>
          <div class="ct-chg-pill ${cls}">${sign} ${Math.abs(chg).toFixed(2)}%</div>
        </div>`;
    });
    html += "</div>";
    $("#market-body").html(html);
}

function updateKpiFromTicker() {
    // Update today PnL color
    const pnlEl = document.getElementById("kpi-pnl");
    if (pnlEl) {
        const val = parseFloat(pnlEl.dataset.value || 0);
        pnlEl.className = "ct-kpi-value " + (val >= 0 ? "ct-buy" : "ct-sell");
    }
}

/* ══════════════════════════════════════════════════════════════════════
   ORDER BOOK
══════════════════════════════════════════════════════════════════════ */
async function loadOrderBook(symbol) {
    if (!symbol) return;
    const pair = toPair(symbol);
    try {
        const resp = await fetch(COINDCX_OB + encodeURIComponent(pair));
        if (!resp.ok) { renderOBUnavailable(symbol); return; }
        const data = await resp.json();
        renderOrderBook(symbol, data);
    } catch (e) { renderOBUnavailable(symbol); }
}

function renderOrderBook(symbol, data) {
    const bids = (data.bids || []).slice(0, 10);
    const asks = (data.asks || []).slice(0, 10);
    if (!bids.length && !asks.length) { renderOBUnavailable(symbol); return; }

    const maxBidVol = Math.max(...bids.map(b => parseFloat(b[1] || b.quantity || 0)));
    const maxAskVol = Math.max(...asks.map(a => parseFloat(a[1] || a.quantity || 0)));

    const bidRows = bids.map(b => {
        const price = parseFloat(b[0] || b.price);
        const qty   = parseFloat(b[1] || b.quantity);
        const pct   = maxBidVol > 0 ? (qty / maxBidVol * 100).toFixed(0) : 0;
        return `<div class="ct-ob-row ct-ob-bid">
          <div class="ct-depth-bar" style="width:${pct}%"></div>
          <div class="ct-ob-price">₹${fmt(price)}</div>
          <div class="ct-ob-qty">${fmtQty(qty)}</div>
        </div>`;
    }).join("");

    const askRows = asks.map(a => {
        const price = parseFloat(a[0] || a.price);
        const qty   = parseFloat(a[1] || a.quantity);
        const pct   = maxAskVol > 0 ? (qty / maxAskVol * 100).toFixed(0) : 0;
        return `<div class="ct-ob-row ct-ob-ask">
          <div class="ct-depth-bar" style="width:${pct}%"></div>
          <div class="ct-ob-price">₹${fmt(price)}</div>
          <div class="ct-ob-qty">${fmtQty(qty)}</div>
        </div>`;
    }).join("");

    const bestBid = bids.length ? parseFloat(bids[0][0] || bids[0].price) : 0;
    const bestAsk = asks.length ? parseFloat(asks[0][0] || asks[0].price) : 0;
    const spread  = bestAsk > 0 && bestBid > 0 ? ((bestAsk - bestBid) / bestBid * 100).toFixed(3) : "—";

    $("#ob-symbol").text(shortSym(normalizeMarket(symbol)));
    $("#orderbook-body").html(`
      <div class="ct-ob-wrap">
        <div class="ct-ob-side">
          <div class="ct-ob-header"><span>PRICE</span><span style="text-align:right">QTY</span></div>
          ${bidRows}
        </div>
        <div class="ct-ob-side">
          <div class="ct-ob-header"><span>PRICE</span><span style="text-align:right">QTY</span></div>
          ${askRows}
        </div>
      </div>
      <div class="ct-ob-spread">Spread: ${spread}%&nbsp;&nbsp;|&nbsp;&nbsp;Bid ₹${fmt(bestBid)}&nbsp;&nbsp;|&nbsp;&nbsp;Ask ₹${fmt(bestAsk)}</div>
    `);
}

function renderOBUnavailable(symbol) {
    $("#ob-symbol").text(shortSym(normalizeMarket(symbol)));
    $("#orderbook-body").html('<div class="ct-empty-state">Order book unavailable for this pair</div>');
}

/* ══════════════════════════════════════════════════════════════════════
   OPEN POSITIONS
══════════════════════════════════════════════════════════════════════ */
function loadPositions() {
    frappe.call({
        method: "coin_trader.trader.get_open_positions",
        callback(r) {
            const positions = r.message || [];
            renderPositions(positions);
            updateKpiOpen(positions.length);
        },
    });
}

function renderPositions(positions) {
    const wrap = document.getElementById("positions-table-wrap");
    const badge = document.getElementById("pos-count-badge");
    if (badge) badge.textContent = positions.length;

    if (!positions.length) {
        wrap.innerHTML = '<div class="ct-empty-state">No open positions</div>';
        return;
    }

    wrap.innerHTML = positions.map(p => {
        const pnl    = parseFloat(p.pnl_inr || 0);
        const pnlPct = parseFloat(p.pnl_pct || 0);
        const cls    = pnl >= 0 ? "is-profit ct-buy" : "is-loss ct-sell";
        const sign   = pnl >= 0 ? "+" : "";
        const ticker = _tickerData[normalizeMarket(p.symbol)];
        const curPri = ticker ? `₹${fmt(ticker.last_price)}` : "—";

        return `<div class="ct-pos-card ${pnl >= 0 ? "is-profit" : "is-loss"}">
          <div class="ct-pos-symbol">${p.symbol}</div>
          <div class="ct-pos-row"><div class="ct-pos-lbl">Entry Price</div><div class="ct-pos-val">₹${fmt(p.buy_price)}</div></div>
          <div class="ct-pos-row"><div class="ct-pos-lbl">Current</div><div class="ct-pos-val">${curPri}</div></div>
          <div class="ct-pos-row"><div class="ct-pos-lbl">Stop-Loss</div><div class="ct-pos-val ct-sell">₹${fmt(p.stop_loss)}</div></div>
          <div class="ct-pos-row"><div class="ct-pos-lbl">Target</div><div class="ct-pos-val ct-buy">₹${fmt(p.target_price)}</div></div>
          <div class="ct-pos-row"><div class="ct-pos-lbl">Qty</div><div class="ct-pos-val">${fmtQty(p.quantity)}</div></div>
          <div class="ct-pos-row"><div class="ct-pos-lbl">Value</div><div class="ct-pos-val">₹${fmt((p.buy_price||0)*(p.quantity||0))}</div></div>
          <div class="ct-pos-pnl ${cls}">${sign}₹${fmt(pnl)} <span style="font-size:13px">(${sign}${pnlPct.toFixed(2)}%)</span></div>
          <button class="ct-btn ct-btn-danger ct-btn-xs ct-pos-close"
                  onclick="CT_DASH.close_position('${p.name}', '${p.symbol}')">✕ Close</button>
        </div>`;
    }).join("");
}

function updateKpiOpen(count) {
    if (count !== undefined) {
        const el = document.getElementById("kpi-open");
        if (el) el.textContent = count;
    }
}

/* ══════════════════════════════════════════════════════════════════════
   FUTURE ORDERS (CT Future Order)
══════════════════════════════════════════════════════════════════════ */
function loadFutureOrders() {
    frappe.call({
        method: "frappe.client.get_list",
        args: {
            doctype: "CT Future Order",
            fields:  ["symbol", "entry_price", "target_price", "stop_loss", "creation"],
            filters: { user: frappe.session.user },
            limit:   20,
            order_by:"creation desc",
        },
        callback(r) {
            renderFutureOrders(r.message || []);
            const el = document.getElementById("kpi-pending");
            if (el) el.textContent = (r.message || []).length;
        },
    });
}

function renderFutureOrders(orders) {
    const wrap = document.getElementById("future-orders-wrap");
    if (!orders.length) { wrap.innerHTML = '<div class="ct-empty-state">No pending future orders</div>'; return; }

    let html = `<table class="ct-table">
      <thead><tr>
        <th>COIN</th><th>CURRENT PRICE</th><th>WAIT FOR (ENTRY)</th>
        <th>GAP TO ENTRY</th><th>TARGET</th><th>STOP-LOSS</th>
        <th>QUEUED AT</th><th>EXPIRES IN</th>
      </tr></thead><tbody>`;

    orders.forEach(o => {
        const mkt      = normalizeMarket(o.symbol);
        const ticker   = _tickerData[mkt];
        const curPrice = ticker ? parseFloat(ticker.last_price) : null;
        const entry    = parseFloat(o.entry_price || 0);
        const gap      = curPrice && entry ? ((entry - curPrice) / curPrice * 100) : null;
        const gapStr   = gap !== null ? `${gap >= 0 ? "+" : ""}${gap.toFixed(2)}%` : "—";
        const gapCls   = gap === null ? "" : (gap >= 0 ? "ct-gap-pos" : "ct-gap-neg");
        const createdAt = o.creation ? new Date(o.creation).toLocaleTimeString("en-IN", {hour:"2-digit", minute:"2-digit"}) : "—";
        const ageMs    = o.creation ? Date.now() - new Date(o.creation).getTime() : 0;
        const expiresMs= 24*60*60*1000 - ageMs;
        const expStr   = expiresMs < 60000 ? "Expiring..." : fmtDuration(expiresMs);
        const expCls   = expiresMs < 60000 ? "ct-expiring" : "";

        html += `<tr>
          <td><strong>${o.symbol}</strong></td>
          <td>${curPrice ? "₹" + fmt(curPrice) : "—"}</td>
          <td class="ct-accent">₹${fmt(entry)}</td>
          <td class="${gapCls}">${gapStr}</td>
          <td class="ct-buy">₹${fmt(o.target_price)}</td>
          <td class="ct-sell">₹${fmt(o.stop_loss)}</td>
          <td class="ct-muted-text">${createdAt}</td>
          <td class="${expCls}">${expStr}</td>
        </tr>`;
    });

    html += "</tbody></table>";
    wrap.innerHTML = html;
}

/* ══════════════════════════════════════════════════════════════════════
   SIGNAL PROBE
══════════════════════════════════════════════════════════════════════ */
function runProbe() {
    const symbol   = $("#symbol-select").val();
    const interval = $("#interval-select").val();
    if (!symbol) { frappe.show_alert({ message: "Select a symbol first", indicator: "orange" }, 3); return; }

    $("#prob-spinner").removeClass("hidden");
    $("#prob-body").html('<div class="ct-loading">⟳ Fetching AI signal…</div>');

    frappe.call({
        method: "coin_trader.ai_adapter.run_ai_validation_api",
        args: { symbol, interval },
        callback(r) {
            $("#prob-spinner").addClass("hidden");
            if (r.exc) { $("#prob-body").html(`<div class="ct-error">Error: ${r.exc}</div>`); return; }
            renderSignal(r.message || r);
        },
        error() { $("#prob-spinner").addClass("hidden"); $("#prob-body").html('<div class="ct-error">Network error</div>'); }
    });
}

function renderSignal(d) {
    const pred  = d.ai_signal || d.prediction || "HOLD";
    const conf  = parseFloat(d.ai_confidence || d.confidence_score || 0);
    const price = parseFloat(d.close || 0);
    const sl    = parseFloat(d.ai_stop_loss || d.suggested_stoploss || 0);
    const tgt   = parseFloat(d.ai_target || d.suggested_target || 0);
    const atr   = parseFloat(d.atr || 0);
    const reason= d.ai_reason || d.reason || "";
    const flags = d.risk_flags || [];

    const heroClass = pred === "BUY" ? "is-buy" : pred === "SELL" ? "is-sell" : "";
    const dirClass  = pred === "BUY" ? "ct-buy" : pred === "SELL" ? "ct-sell" : "ct-hold";
    const badgeCls  = pred === "BUY" ? "ct-badge-buy" : pred === "SELL" ? "ct-badge-sell" : "ct-badge-hold";
    const confCls   = conf >= 85 ? "ct-buy" : conf >= 70 ? "ct-accent" : "ct-sell";

    const rr = sl > 0 && tgt > 0 && price > 0
        ? (Math.abs(tgt - price) / Math.abs(price - sl)).toFixed(2) : "—";

    let html = `
      <div class="ct-signal-hero ${heroClass}">
        <div class="ct-signal-direction ${dirClass}">${pred}</div>
        <div class="ct-conf-wrap">
          <div class="ct-conf-label">AI Confidence</div>
          <div class="ct-conf-bar-track"><div class="ct-conf-bar-fill" style="width:${conf}%"></div></div>
          <div class="ct-conf-value ${confCls}">${conf.toFixed(0)}%</div>
        </div>
        <span class="ct-badge ${badgeCls}" style="align-self:flex-start">${pred}</span>
      </div>

      <div class="ct-signal-meta">
        <div class="ct-meta-box">
          <div class="ct-meta-label">Price</div>
          <div class="ct-meta-value">₹${fmt(price)}</div>
        </div>
        <div class="ct-meta-box">
          <div class="ct-meta-label">ATR</div>
          <div class="ct-meta-value ct-muted-text">${fmt(atr)}</div>
        </div>
        <div class="ct-meta-box">
          <div class="ct-meta-label">Stop-Loss</div>
          <div class="ct-meta-value ct-sell">₹${fmt(sl)}</div>
        </div>
        <div class="ct-meta-box">
          <div class="ct-meta-label">Target</div>
          <div class="ct-meta-value ct-buy">₹${fmt(tgt)}</div>
        </div>
        <div class="ct-meta-box">
          <div class="ct-meta-label">Risk : Reward</div>
          <div class="ct-meta-value ct-accent">1 : ${rr}</div>
        </div>
        <div class="ct-meta-box">
          <div class="ct-meta-label">Min Confidence</div>
          <div class="ct-meta-value ${conf >= CT.min_conf ? "ct-buy" : "ct-sell"}">${CT.min_conf}%</div>
        </div>
      </div>`;

    if (reason) {
        html += `<div class="ct-ai-reason">💬 ${reason}</div>`;
    }
    if (flags && flags.length) {
        html += `<div class="ct-risk-flags">${flags.map(f => `<span class="ct-flag">⚠ ${f}</span>`).join("")}</div>`;
    }

    document.getElementById("prob-body").innerHTML = html;
}

/* ══════════════════════════════════════════════════════════════════════
   SCAN
══════════════════════════════════════════════════════════════════════ */
function runScan() {
    if (_scanInFlight) return;
    _scanInFlight = true;
    const btn  = document.getElementById("btn-scan");
    const txt  = document.getElementById("btn-scan-text");
    btn.disabled = true;
    txt.textContent = "⟳ Scanning…";

    frappe.call({
        method: "coin_trader.scanner.run_scanner_api",
        callback(r) {
            _scanInFlight = false;
            btn.disabled  = false;
            txt.textContent = "▶ Run Scan";
            const results = r.message || [];
            renderScanResults(results);
            updateLastScan();
            loadPositions();
        },
        error() { _scanInFlight = false; btn.disabled = false; txt.textContent = "▶ Run Scan"; }
    });
}

function renderScanResults(results) {
    const wrap  = document.getElementById("scan-results-wrap");
    const badge = document.getElementById("scan-symbol-count");
    if (badge) badge.textContent = results.length + " symbols";

    if (!results.length) { wrap.innerHTML = '<div class="ct-empty-state">No scan results yet</div>'; return; }

    let html = `<div class="ct-scan-header">
      <span>SYMBOL</span><span>SIGNAL</span><span>CONFIDENCE</span><span>REASON</span><span style="text-align:right">STATUS</span>
    </div>`;

    results.forEach(r => {
        const pred     = r.ai_signal || r.prediction || "HOLD";
        const conf     = parseFloat(r.ai_confidence || r.confidence_score || 0);
        const approved = r.risk_approved;
        const executed = r.executed;
        const reason   = r.reject_reason || r.approve_reason || r.reason || "—";
        const badgeCls = pred === "BUY" ? "ct-badge-buy" : pred === "SELL" ? "ct-badge-sell" : "ct-badge-hold";
        const confCls  = conf >= 85 ? "ct-buy" : conf >= 70 ? "ct-accent" : "ct-muted-text";

        let statusBadge;
        if (executed)       statusBadge = `<span class="ct-badge ct-badge-live">EXECUTED</span>`;
        else if (approved)  statusBadge = `<span class="ct-badge ct-badge-success">APPROVED</span>`;
        else                statusBadge = `<span class="ct-badge ct-badge-danger">REJECTED</span>`;

        html += `<div class="ct-scan-row">
          <span class="ct-scan-sym">${r.symbol || "—"}</span>
          <span><span class="ct-badge ${badgeCls}">${pred}</span></span>
          <span class="ct-scan-conf ${confCls}">${conf.toFixed(0)}%</span>
          <span class="ct-scan-reason" title="${escHtml(reason)}">${escHtml(reason)}</span>
          <span class="ct-scan-status">${statusBadge}</span>
        </div>`;
    });

    wrap.innerHTML = html;
}

/* ══════════════════════════════════════════════════════════════════════
   TRADE HISTORY
══════════════════════════════════════════════════════════════════════ */
function loadTradeHistory() {
    frappe.call({
        method: "frappe.client.get_list",
        args: {
            doctype: "CT Trade Log",
            fields:  ["symbol", "action", "price", "quantity", "pnl_inr", "pnl_pct", "reason", "is_paper_trade", "trade_time"],
            filters: { user: frappe.session.user },
            limit:   30,
            order_by:"trade_time desc",
        },
        callback(r) { renderTradeHistory(r.message || []); }
    });
}

function renderTradeHistory(trades) {
    const wrap  = document.getElementById("trade-history-wrap");
    const badge = document.getElementById("hist-count-badge");
    if (badge) badge.textContent = trades.length + " trades";

    if (!trades.length) { wrap.innerHTML = '<div class="ct-empty-state">No trade history yet</div>'; return; }

    let totalPnl = 0;
    let html = `<table class="ct-table">
      <thead><tr>
        <th>Symbol</th><th>Action</th><th>Price</th><th>Qty</th>
        <th>PnL (INR)</th><th>PnL %</th><th>Reason</th><th>Mode</th><th>Time</th>
      </tr></thead><tbody>`;

    trades.forEach(t => {
        const pnl  = parseFloat(t.pnl_inr || 0);
        const pnlP = parseFloat(t.pnl_pct || 0);
        const cls  = pnl > 0 ? "ct-buy" : pnl < 0 ? "ct-sell" : "";
        const sign = pnl >= 0 ? "+" : "";
        totalPnl  += pnl;
        const actCls = (t.action || "").toUpperCase() === "BUY" ? "ct-buy" : "ct-sell";
        const dt   = t.trade_time ? new Date(t.trade_time).toLocaleString("en-IN", {dateStyle:"short", timeStyle:"short"}) : "—";
        const mode = t.is_paper_trade ? '<span class="ct-badge ct-badge-paper">PAPER</span>' : '<span class="ct-badge ct-badge-live">LIVE</span>';

        html += `<tr>
          <td><strong>${t.symbol||"—"}</strong></td>
          <td class="${actCls} " style="font-weight:700">${(t.action||"").toUpperCase()}</td>
          <td>₹${fmt(t.price)}</td>
          <td>${fmtQty(t.quantity)}</td>
          <td class="${cls}" style="font-weight:700">${pnl !== 0 ? sign + "₹" + fmt(Math.abs(pnl)) : "—"}</td>
          <td class="${cls}">${pnlP !== 0 ? sign + pnlP.toFixed(2) + "%" : "—"}</td>
          <td class="ct-muted-text" style="max-width:180px;overflow:hidden;text-overflow:ellipsis" title="${escHtml(t.reason||"")}">${escHtml(t.reason||"—")}</td>
          <td>${mode}</td>
          <td class="ct-muted-text">${dt}</td>
        </tr>`;
    });

    html += `</tbody></table>`;

    const totalCls = totalPnl >= 0 ? "ct-buy" : "ct-sell";
    html += `<div style="margin-top:10px;text-align:right;font-size:12px;color:var(--ct-muted)">
      Total P&amp;L shown: <strong class="${totalCls}">${totalPnl >= 0 ? "+" : ""}₹${fmt(Math.abs(totalPnl))}</strong>
    </div>`;

    wrap.innerHTML = html;

    // Update KPI pnl sub-label
    const sub = document.getElementById("kpi-pnl-sub");
    if (sub) sub.textContent = `${trades.filter(t => t.action !== "BUY").length} trades today`;
}

/* ══════════════════════════════════════════════════════════════════════
   BACKTEST
══════════════════════════════════════════════════════════════════════ */
function runBacktest() {
    const symbol   = $("#symbol-select").val();
    const interval = $("#interval-select").val();
    if (!symbol) { frappe.show_alert({ message: "Select a symbol first", indicator: "orange" }, 3); return; }

    $("#card-backtest").removeClass("hidden");
    document.getElementById("backtest-body").innerHTML = '<div class="ct-loading">⟳ Running walk-forward backtest…</div>';
    document.getElementById("card-backtest").scrollIntoView({ behavior: "smooth" });

    frappe.call({
        method: "coin_trader.backtest_engine.run_backtest_api",
        args: { symbol, interval, limit: 1000, min_confidence: CT.min_conf },
        callback(r) {
            const res = r.message || {};
            if (!res.success) {
                document.getElementById("backtest-body").innerHTML = `<div class="ct-error">❌ ${escHtml(res.error || "Backtest failed")}</div>`;
                return;
            }
            renderBacktest(res, symbol, interval);
        },
        error() { document.getElementById("backtest-body").innerHTML = '<div class="ct-error">Network error running backtest</div>'; }
    });
}

function renderBacktest(res, symbol, interval) {
    const m  = res.metrics || {};
    const tr = res.trades  || [];

    const winColor  = (m.win_rate || 0) >= 55 ? "ct-buy" : "ct-sell";
    const pfColor   = (m.profit_factor || 0) >= 1.5 ? "ct-buy" : "ct-sell";
    const npColor   = (m.net_profit_pct || 0) >= 0 ? "ct-buy" : "ct-sell";
    const ddColor   = (m.max_drawdown_pct || 0) <= 15 ? "ct-buy" : "ct-sell";
    const shrpColor = (m.sharpe_ratio || 0) >= 1 ? "ct-buy" : "ct-sell";

    let html = `<div style="margin-bottom:14px;font-weight:700;font-size:14px">${symbol} · ${interval} · Walk-Forward Backtest</div>
    <div class="ct-bt-grid">
      <div class="ct-bt-stat"><div class="ct-bt-label">Total Trades</div><div class="ct-bt-value">${m.total_trades || 0}</div></div>
      <div class="ct-bt-stat"><div class="ct-bt-label">Win Rate</div><div class="ct-bt-value ${winColor}">${(m.win_rate||0).toFixed(1)}%</div></div>
      <div class="ct-bt-stat"><div class="ct-bt-label">Profit Factor</div><div class="ct-bt-value ${pfColor}">${(m.profit_factor||0).toFixed(2)}x</div></div>
      <div class="ct-bt-stat"><div class="ct-bt-label">Net Profit</div><div class="ct-bt-value ${npColor}">${(m.net_profit_pct||0) >= 0 ? "+" : ""}${(m.net_profit_pct||0).toFixed(2)}%</div></div>
      <div class="ct-bt-stat"><div class="ct-bt-label">Max Drawdown</div><div class="ct-bt-value ${ddColor}">${(m.max_drawdown_pct||0).toFixed(2)}%</div></div>
      <div class="ct-bt-stat"><div class="ct-bt-label">Sharpe Ratio</div><div class="ct-bt-value ${shrpColor}">${(m.sharpe_ratio||0).toFixed(2)}</div></div>
      <div class="ct-bt-stat"><div class="ct-bt-label">Avg Win</div><div class="ct-bt-value ct-buy">+${(m.avg_win_pct||0).toFixed(2)}%</div></div>
      <div class="ct-bt-stat"><div class="ct-bt-label">Avg Loss</div><div class="ct-bt-value ct-sell">${(m.avg_loss_pct||0).toFixed(2)}%</div></div>
      <div class="ct-bt-stat"><div class="ct-bt-label">Expectancy</div><div class="ct-bt-value">${(m.expectancy_pct||0).toFixed(3)}%</div></div>
      <div class="ct-bt-stat"><div class="ct-bt-label">Avg Hold</div><div class="ct-bt-value">${(m.avg_duration_candles||0).toFixed(1)} candles</div></div>
    </div>`;

    if (tr.length) {
        html += `<div style="font-size:12px;color:var(--ct-muted);margin-bottom:8px">Sample trades (up to 50)</div>
        <table class="ct-table"><thead><tr>
          <th>#</th><th>Direction</th><th>Entry</th><th>Exit</th><th>PnL %</th><th>Exit Reason</th><th>Hold (candles)</th>
        </tr></thead><tbody>`;

        tr.slice(0, 50).forEach((t, i) => {
            const cls  = (t.pnl_pct || 0) >= 0 ? "ct-buy" : "ct-sell";
            const sign = (t.pnl_pct || 0) >= 0 ? "+" : "";
            const dirCls = (t.prediction||"").toUpperCase() === "BUY" ? "ct-buy" : "ct-sell";
            html += `<tr>
              <td class="ct-muted-text">${i+1}</td>
              <td class="${dirCls} " style="font-weight:700">${t.prediction||"—"}</td>
              <td>₹${fmt(t.entry_price)}</td>
              <td>₹${fmt(t.exit_price)}</td>
              <td class="${cls}" style="font-weight:700">${sign}${(t.pnl_pct||0).toFixed(2)}%</td>
              <td class="ct-muted-text">${t.exit_reason||"—"}</td>
              <td class="ct-muted-text">${t.duration_candles||0}</td>
            </tr>`;
        });
        html += "</tbody></table>";
    }

    document.getElementById("backtest-body").innerHTML = html;
}

/* ══════════════════════════════════════════════════════════════════════
   CLOSE POSITION
══════════════════════════════════════════════════════════════════════ */
function close_position(name, symbol) {
    frappe.confirm(`Close position for <b>${symbol}</b> at market price?`, () => {
        frappe.call({
            method: "coin_trader.trader.manual_close_position",
            args: { position_name: name },
            callback(r) {
                if (r.message && r.message.success) {
                    frappe.show_alert({ message: `${symbol} closed`, indicator: "green" }, 4);
                    loadPositions();
                    loadTradeHistory();
                } else {
                    frappe.msgprint(r.message && r.message.error ? r.message.error : "Close failed");
                }
            }
        });
    });
}

window.CT_DASH = { close_position };

/* ══════════════════════════════════════════════════════════════════════
   HELPERS
══════════════════════════════════════════════════════════════════════ */

function setSymbol(market) {
    // Convert market key back to symbol and update selects + orderbook
    const sym = Object.fromEntries(CT.symbols.map(s => [normalizeMarket(s), s]))[market] || market;
    const sel = document.getElementById("symbol-select");
    if (sel) { sel.value = sym; }
    _currentOBSym = sym;
    loadOrderBook(sym);
}
window.setSymbol = setSymbol;

// "BTCINR" → "B-BTC_INR" (CoinDCX market key pattern for INR pairs)
function normalizeMarket(symbol) {
    if (!symbol) return "";
    // Already normalized (contains hyphen)
    if (symbol.includes("-")) return symbol;
    // Common INR pairs: BTCINR → B-BTC_INR
    const m = symbol.match(/^([A-Z0-9]+)(INR|USDT|BTC|ETH|BNB)$/i);
    if (m) return `B-${m[1]}_${m[2].toUpperCase()}`;
    return symbol;
}

// "B-BTC_INR" → "BTCINR" label
function shortSym(market) {
    return market.replace(/^[A-Z]-/, "").replace("_", "");
}

// "BTCINR" → pair for orderbook API  "B-BTC_INR"
function toPair(symbol) {
    return normalizeMarket(symbol);
}

function updateKpiOpen() {
    frappe.call({
        method: "frappe.client.get_count",
        args: { doctype: "CT Open Position", filters: { user: frappe.session.user, status: "Open" } },
        callback(r) { const el = document.getElementById("kpi-open"); if (el) el.textContent = r.message || 0; }
    });
}

function updateLastScan() {
    const now = new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const el  = document.getElementById("last-scan-time");
    if (el) el.textContent = `Last scan: ${now}`;
}

// Number formatter
function fmt(n) {
    const v = parseFloat(n);
    if (isNaN(v)) return "—";
    if (v >= 1_00_000)  return (v / 1_00_000).toFixed(2) + "L";
    if (v >= 1_000)     return v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
    if (v >= 1)         return v.toFixed(2);
    if (v >= 0.01)      return v.toFixed(4);
    return v.toFixed(6);
}

function fmtVol(n) {
    const v = parseFloat(n);
    if (isNaN(v)) return "—";
    if (v >= 1e7) return (v / 1e7).toFixed(2) + "Cr";
    if (v >= 1e5) return (v / 1e5).toFixed(2) + "L";
    if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
    return v.toFixed(2);
}

function fmtQty(n) {
    const v = parseFloat(n);
    if (isNaN(v)) return "—";
    if (v >= 1) return v.toFixed(4);
    return v.toFixed(6);
}

function fmtDuration(ms) {
    const h = Math.floor(ms / 3.6e6);
    const m = Math.floor((ms % 3.6e6) / 60000);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function escHtml(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

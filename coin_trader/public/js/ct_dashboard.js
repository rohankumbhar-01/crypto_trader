/**
 * Coin Trader · Ultra Fullscreen Dashboard
 * --------------------------------------------
 * Architecture:
 *   - Single snapshot endpoint polled every 5s (everything except chart + ob)
 *   - Order book polled every 4s for selected pair
 *   - Chart re-fetched on interval/symbol change (lightweight-charts)
 *   - Frappe realtime events for instant trade open/close reactions
 *
 * No CoinDCX direct calls (all proxied server-side via dashboard_api.py).
 */

"use strict";

/* ═ STATE ═════════════════════════════════════════════════════════ */

// Popular INR pairs shown when no active config
const CT_DEFAULT_SYMBOLS = ["BTCINR","ETHINR","XRPINR","SOLINR","ADAINR","DOGINR","BNBINR","MATICUSDT"];

const CT = {
    snapshot:        null,           // last snapshot payload
    chartSymbol:     null,
    chartInterval:   "1h",
    chart:           null,
    candleSeries:    null,
    obSymbol:        null,
    lastPrices:      {},             // market_key -> previous price (for flash)
    pollTimer:       null,
    obTimer:         null,
    chartTimer:      null,
    scanInFlight:    false,
    defaultMode:     false,          // true when showing ticker data without config
};

/* ═ BOOT ══════════════════════════════════════════════════════════ */

frappe.ready(function () {
    initRealtime();
    pollSnapshot();                        // immediate — populates symbols
    loadTradeHistory();
    CT.pollTimer  = setInterval(pollSnapshot, 5000);
    CT.obTimer    = setInterval(refreshOrderBook, 4000);
    CT.chartTimer = setInterval(refreshChart, 30000);

    // Init chart after a short delay so the flex container has real dimensions
    setTimeout(function() {
        initChart();
        // If symbols already loaded by the time chart inits, draw immediately
        if (CT.chartSymbol) refreshChart();
    }, 300);

    // Buttons
    $("#btn-refresh").on("click", () => { pollSnapshot(); refreshOrderBook(); refreshChart(); loadTradeHistory(); });
    $("#btn-scan").on("click", runScan);
    $("#btn-probe").on("click", runProbe);
    $("#btn-backtest").on("click", runBacktest);
    $("#btn-close-bt").on("click", () => $("#backtest-card").addClass("hidden"));

    // Interval tabs
    $("#iv-tabs button").on("click", function () {
        const iv = this.dataset.iv;
        $("#iv-tabs button").removeClass("active");
        $(this).addClass("active");
        CT.chartInterval = iv;
        refreshChart();
    });

    // Symbol selectors
    $("#chart-symbol").on("change", function () { CT.chartSymbol = this.value; refreshChart(); refreshOrderBook(); });
    $("#probe-symbol").on("change", function () { /* user just selects; runs on button */ });
});

/* ═ REALTIME ══════════════════════════════════════════════════════ */

function initRealtime() {
    frappe.realtime.on("ct_scan_complete",   d => { renderScan(d.signals || d.results || []); pollSnapshot(); });
    frappe.realtime.on("ct_position_opened", () => { pollSnapshot(); frappe.show_alert({ message: "✅ Position opened", indicator: "green" }, 3); });
    frappe.realtime.on("ct_position_closed", () => { pollSnapshot(); loadTradeHistory(); frappe.show_alert({ message: "Position closed", indicator: "blue" }, 3); });
}

/* ═ SNAPSHOT POLL ═════════════════════════════════════════════════ */

function pollSnapshot() {
    frappe.call({
        method: "coin_trader.dashboard_api.get_snapshot",
        type:   "GET",
        callback(r) {
            if (!r.message) return;
            const snap = r.message;
            CT.snapshot = snap;

            // Debug: log session user and tracked symbols
            console.log("[CT] session_user:", snap.session_user, "| tracked:", snap.tracked_symbols, "| market keys:", Object.keys(snap.market || {}));

            // Initialise chart/orderbook symbol from tracked list on first load
            if (!CT.chartSymbol && snap.tracked_symbols.length) {
                CT.chartSymbol = snap.tracked_symbols[0];
                CT.obSymbol    = snap.tracked_symbols[0];
                populateSymbolSelectors(snap.tracked_symbols);
                refreshOrderBook();
                // Ensure chart is ready before drawing
                if (CT.chart && CT.candleSeries) {
                    refreshChart();
                } else {
                    setTimeout(function() { initChart(); refreshChart(); }, 400);
                }
            }

            renderMode(snap);
            renderKpis(snap);

            // If no tracked symbols, fetch full public ticker and display default pairs
            if (!snap.tracked_symbols.length) {
                loadPublicTicker();
            } else {
                renderTicker(snap);
                renderMarket(snap);
            }

            renderPositions(snap);
            renderFutureOrders(snap);
            updateLastUpdate();
        },
    });
}

function loadPublicTicker() {
    frappe.call({
        method: "coin_trader.dashboard_api.get_market_ticker",
        type:   "GET",
        callback(r) {
            const tickerArr = r.message || [];
            if (!tickerArr.length) return;

            // Build map
            const map = {};
            tickerArr.forEach(t => { if (t.market) map[t.market] = t; });

            // Pick popular symbols available in the response
            const available = CT_DEFAULT_SYMBOLS.filter(s => map[s]);
            const displaySyms = available.length ? available : tickerArr.slice(0, 8).map(t => t.market);

            // Build synthetic snap.market
            const market = {};
            displaySyms.forEach(s => {
                const t = map[s];
                if (!t) return;
                market[s] = {
                    last_price:     parseFloat(t.last_price || 0),
                    high:           parseFloat(t.high || 0),
                    low:            parseFloat(t.low || 0),
                    volume:         parseFloat(t.volume || 0),
                    bid:            parseFloat(t.bid || 0),
                    ask:            parseFloat(t.ask || 0),
                    change_24_hour: parseFloat(t.change_24_hour || 0),
                };
            });

            // Synthetic snapshot for rendering
            const synthSnap = Object.assign({}, CT.snapshot || {}, {
                market,
                tracked_symbols: displaySyms,
            });

            // Set chart symbol if not set
            if (!CT.chartSymbol && displaySyms.length) {
                CT.chartSymbol = displaySyms[0];
                CT.obSymbol    = displaySyms[0];
                populateSymbolSelectors(displaySyms);
                refreshChart();
                refreshOrderBook();
            }

            renderTicker(synthSnap);
            renderMarket(synthSnap);
        }
    });
}

function updateLastUpdate() {
    const el = document.getElementById("last-update");
    if (el) el.textContent = new Date().toLocaleTimeString("en-IN", { hour:"2-digit", minute:"2-digit", second:"2-digit" });
}

function populateSymbolSelectors(symbols) {
    const opts = symbols.map(s => `<option value="${s}">${s}</option>`).join("");
    $("#chart-symbol").html(opts).val(CT.chartSymbol);
    $("#probe-symbol").html(opts).val(CT.chartSymbol);
}

/* ═ TOP BAR / MODE ════════════════════════════════════════════════ */

function renderMode(snap) {
    const badge = document.getElementById("mode-badge");
    if (snap.dry_run) { badge.textContent = "⬤ PAPER"; badge.className = "ct-mode-badge ct-mode-paper"; }
    else              { badge.textContent = "⬤ LIVE";  badge.className = "ct-mode-badge ct-mode-live";  }
    document.getElementById("warn-no-config").classList.toggle("hidden", snap.is_active);
    document.getElementById("warn-no-model").classList.toggle("hidden", snap.model_trained);
}

/* ═ KPIs ══════════════════════════════════════════════════════════ */

function renderKpis(snap) {
    setText("kpi-balance", "₹" + fmtINR(snap.inr_balance));

    const pnl    = snap.today_pnl || 0;
    const pnlEl  = document.getElementById("kpi-pnl");
    pnlEl.textContent = (pnl >= 0 ? "+" : "") + "₹" + fmtINR(Math.abs(pnl));
    pnlEl.className   = "ct-kpi-value " + (pnl > 0 ? "ct-buy" : pnl < 0 ? "ct-sell" : "");
    setText("kpi-trades-sub", `${snap.today_trades || 0} trades today`);

    setText("kpi-open", snap.open_positions_count);
    setText("kpi-max-sub", `Max ${snap.config.max_positions || 5} allowed`);

    setText("kpi-pending", snap.future_orders_count);

    const unreal = (snap.open_positions || []).reduce((a, p) => a + (parseFloat(p.pnl_inr) || 0), 0);
    const unEl   = document.getElementById("kpi-unreal");
    unEl.textContent = (unreal >= 0 ? "+" : "") + "₹" + fmtINR(Math.abs(unreal));
    unEl.className   = "ct-kpi-value " + (unreal > 0 ? "ct-buy" : unreal < 0 ? "ct-sell" : "");

    setText("kpi-ai", snap.ai_provider ? snap.ai_provider.charAt(0).toUpperCase() + snap.ai_provider.slice(1) : "—");
    setText("kpi-coins-sub", `${(snap.tracked_symbols || []).length} coins tracked`);
}

/* ═ TICKER STRIP ══════════════════════════════════════════════════ */

function renderTicker(snap) {
    const strip = document.getElementById("ticker-strip");
    if (!strip) return;
    const items = Object.entries(snap.market || {}).map(([mkt, t]) => {
        const chg  = t.change_24_hour || 0;
        const cls  = chg >= 0 ? "ct-chg-up ct-buy" : "ct-chg-down ct-sell";
        const sign = chg >= 0 ? "▲" : "▼";
        return `<div class="ct-ticker-item" data-mkt="${mkt}">
          <span class="ct-ticker-sym">${shortSym(mkt)}</span>
          <span class="ct-ticker-price">₹${fmt(t.last_price)}</span>
          <span class="ct-ticker-chg ${cls}">${sign} ${Math.abs(chg).toFixed(2)}%</span>
        </div>`;
    }).join("");
    strip.innerHTML = items || `<span class="ct-empty">Loading market…</span>`;
    strip.querySelectorAll(".ct-ticker-item").forEach(el => {
        el.addEventListener("click", () => {
            const sym = mktToSymbol(el.dataset.mkt, snap.tracked_symbols);
            if (!sym) return;
            CT.chartSymbol = sym; CT.obSymbol = sym;
            $("#chart-symbol").val(sym); $("#probe-symbol").val(sym);
            refreshChart(); refreshOrderBook();
        });
    });
}

/* ═ MARKET OVERVIEW ═══════════════════════════════════════════════ */

function renderMarket(snap) {
    const wrap = document.getElementById("market-body");
    const symbols = snap.tracked_symbols || [];
    if (!symbols.length) { wrap.innerHTML = `<div class="ct-loading">Loading market data…</div>`; return; }

    let html = `<div class="ct-market-list">`;
    symbols.forEach(sym => {
        const mkt = symbolToMkt(sym);
        const t   = snap.market[mkt];
        if (!t) {
            html += `<div class="ct-market-row" data-sym="${sym}"><div><div class="ct-m-sym">${sym}</div><div class="ct-m-info">No data</div></div><div></div><div></div></div>`;
            return;
        }
        const chg  = t.change_24_hour || 0;
        const sign = chg >= 0 ? "▲" : "▼";
        const cls  = chg >= 0 ? "ct-chg-up" : "ct-chg-down";
        const pcls = chg >= 0 ? "ct-buy"    : "ct-sell";
        const prev = CT.lastPrices[mkt];
        const flashCls = (prev && prev !== t.last_price)
            ? (t.last_price > prev ? "ct-flash-up" : "ct-flash-down") : "";
        CT.lastPrices[mkt] = t.last_price;

        const active = sym === CT.chartSymbol ? "active" : "";
        html += `<div class="ct-market-row ${active} ${flashCls}" data-sym="${sym}">
          <div>
            <div class="ct-m-sym">${sym}</div>
            <div class="ct-m-info">H: ₹${fmt(t.high)}  ·  L: ₹${fmt(t.low)}</div>
          </div>
          <div style="text-align:right;">
            <div class="ct-m-price ${pcls}">₹${fmt(t.last_price)}</div>
            <div class="ct-m-vol">Vol: ${fmtVol(t.volume)}</div>
          </div>
          <div class="ct-chg-pill ${cls}">${sign} ${Math.abs(chg).toFixed(2)}%</div>
        </div>`;
    });
    html += `</div>`;
    wrap.innerHTML = html;

    wrap.querySelectorAll(".ct-market-row").forEach(el => {
        el.addEventListener("click", () => {
            const sym = el.dataset.sym;
            CT.chartSymbol = sym; CT.obSymbol = sym;
            $("#chart-symbol").val(sym); $("#probe-symbol").val(sym);
            refreshChart(); refreshOrderBook();
            wrap.querySelectorAll(".ct-market-row").forEach(r => r.classList.remove("active"));
            el.classList.add("active");
        });
    });
}

/* ═ CHART (TradingView Lightweight Charts) ════════════════════════ */

function initChart() {
    const container = document.getElementById("price-chart");
    if (!container || !window.LightweightCharts) return;
    if (CT.chart) return;   // already initialised

    // Use parent height if container hasn't painted yet
    const parent = container.parentElement;
    const w = container.clientWidth || (parent && parent.clientWidth) || 600;
    const h = container.clientHeight > 60 ? container.clientHeight
            : (parent && parent.clientHeight > 60 ? parent.clientHeight : 340);

    container.style.width  = "100%";
    container.style.height = h + "px";

    CT.chart = LightweightCharts.createChart(container, {
        width:  w,
        height: h,
        layout: { background: { color: "#0f1525" }, textColor: "#93a0c2" },
        grid:   { vertLines: { color: "#1c2540" }, horzLines: { color: "#1c2540" } },
        crosshair: { mode: 1 },
        timeScale: { borderColor: "#28335a", timeVisible: true, secondsVisible: false },
        rightPriceScale: { borderColor: "#28335a" },
    });

    CT.candleSeries = CT.chart.addCandlestickSeries({
        upColor:        "#10d57b",
        downColor:      "#ff4757",
        borderUpColor:  "#10d57b",
        borderDownColor:"#ff4757",
        wickUpColor:    "#10d57b",
        wickDownColor:  "#ff4757",
    });

    new ResizeObserver(() => {
        if (CT.chart && container.clientWidth) {
            CT.chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
        }
    }).observe(container);
}

function chartLimit(iv) {
    return { "1m": 500, "5m": 500, "15m": 500, "30m": 500, "1h": 500, "4h": 500, "1d": 1000 }[iv] || 500;
}

function refreshChart() {
    if (!CT.chart || !CT.candleSeries || !CT.chartSymbol) return;
    frappe.call({
        method: "coin_trader.dashboard_api.get_candles_data",
        type:   "GET",
        args:   { symbol: CT.chartSymbol, interval: CT.chartInterval, limit: chartLimit(CT.chartInterval) },
        callback(r) {
            const data = r.message || [];
            if (!data.length) return;
            CT.candleSeries.setData(data);
            const last = data[data.length - 1];
            setText("chart-price", "₹" + fmt(last.close));
            const priceEl = document.getElementById("chart-price");
            if (priceEl) priceEl.className = "ct-chart-price " + (last.close >= last.open ? "ct-buy" : "ct-sell");
        },
    });
}

/* ═ ORDER BOOK ════════════════════════════════════════════════════ */

function refreshOrderBook() {
    if (!CT.obSymbol) return;
    frappe.call({
        method: "coin_trader.dashboard_api.get_orderbook",
        type:   "GET",
        args:   { pair: CT.obSymbol },
        callback(r) { renderOrderBook(CT.obSymbol, r.message || {}); },
    });
}

function renderOrderBook(symbol, data) {
    setText("ob-symbol", symbol);
    const bids = (data.bids || []).slice(0, 10);
    const asks = (data.asks || []).slice(0, 10);
    if (!bids.length && !asks.length) {
        document.getElementById("orderbook-body").innerHTML = `<div class="ct-empty">Order book unavailable</div>`;
        return;
    }

    const maxBidQ = Math.max(...bids.map(b => b[1] || 0));
    const maxAskQ = Math.max(...asks.map(a => a[1] || 0));

    const bidRows = bids.map(b => {
        const w = maxBidQ ? ((b[1] / maxBidQ) * 100).toFixed(0) : 0;
        return `<div class="ct-ob-row ct-ob-bid">
          <div class="ct-bar" style="width:${w}%"></div>
          <div class="ct-ob-p">${fmt(b[0])}</div>
          <div class="ct-ob-q">${fmtQty(b[1])}</div>
        </div>`;
    }).join("");

    const askRows = asks.map(a => {
        const w = maxAskQ ? ((a[1] / maxAskQ) * 100).toFixed(0) : 0;
        return `<div class="ct-ob-row ct-ob-ask">
          <div class="ct-bar" style="width:${w}%"></div>
          <div class="ct-ob-p">${fmt(a[0])}</div>
          <div class="ct-ob-q">${fmtQty(a[1])}</div>
        </div>`;
    }).join("");

    const bestBid = bids[0] ? bids[0][0] : 0;
    const bestAsk = asks[0] ? asks[0][0] : 0;
    const spread  = (bestBid && bestAsk) ? ((bestAsk - bestBid) / bestBid * 100).toFixed(3) : "—";

    document.getElementById("orderbook-body").innerHTML = `
      <div class="ct-ob-cols">
        <div>
          <div class="ct-ob-hd"><span>BID</span><span style="text-align:right">QTY</span></div>
          ${bidRows}
        </div>
        <div>
          <div class="ct-ob-hd"><span>ASK</span><span style="text-align:right">QTY</span></div>
          ${askRows}
        </div>
      </div>
      <div class="ct-ob-spread">Spread ${spread}%  ·  Bid ₹${fmt(bestBid)}  ·  Ask ₹${fmt(bestAsk)}</div>`;
}

/* ═ POSITIONS ═════════════════════════════════════════════════════ */

function renderPositions(snap) {
    const wrap = document.getElementById("positions-body");
    setText("pos-count", `${snap.open_positions_count} open`);

    const positions = snap.open_positions || [];
    if (!positions.length) { wrap.innerHTML = `<div class="ct-empty">No open positions</div>`; return; }

    wrap.innerHTML = `<div class="ct-pos-grid">` + positions.map(p => {
        const pnl    = parseFloat(p.pnl_inr || 0);
        const pnlPct = parseFloat(p.pnl_pct || 0);
        const cls    = pnl >= 0 ? "is-profit" : "is-loss";
        const pcls   = pnl >= 0 ? "ct-buy" : "ct-sell";
        const sign   = pnl >= 0 ? "+" : "";
        return `<div class="ct-pos-card ${cls}">
          <div class="ct-pos-sym">${p.symbol}</div>
          <button class="ct-btn ct-btn-danger ct-btn-xs ct-pos-close" onclick="CT_DASH.close_position('${p.name}','${p.symbol}')">✕ Close</button>
          <div class="ct-pos-row"><div class="ct-pos-l">Entry</div><div class="ct-pos-v">₹${fmt(p.buy_price)}</div></div>
          <div class="ct-pos-row"><div class="ct-pos-l">Current</div><div class="ct-pos-v">${p.current_price ? "₹"+fmt(p.current_price) : "—"}</div></div>
          <div class="ct-pos-row"><div class="ct-pos-l">Stop-Loss</div><div class="ct-pos-v ct-sell">₹${fmt(p.stop_loss)}</div></div>
          <div class="ct-pos-row"><div class="ct-pos-l">Target</div><div class="ct-pos-v ct-buy">₹${fmt(p.target_price)}</div></div>
          <div class="ct-pos-row"><div class="ct-pos-l">Qty</div><div class="ct-pos-v">${fmtQty(p.quantity)}</div></div>
          <div class="ct-pos-row"><div class="ct-pos-l">Value</div><div class="ct-pos-v">₹${fmt((p.buy_price||0)*(p.quantity||0))}</div></div>
          <div class="ct-pos-pnl ${pcls}">${sign}₹${fmt(Math.abs(pnl))} <span style="font-size:13px;opacity:0.8">(${sign}${pnlPct.toFixed(2)}%)</span></div>
        </div>`;
    }).join("") + `</div>`;
}

/* ═ FUTURE ORDERS ═════════════════════════════════════════════════ */

function renderFutureOrders(snap) {
    const wrap = document.getElementById("future-body");
    const orders = snap.future_orders || [];
    if (!orders.length) { wrap.innerHTML = `<div class="ct-empty">No pending future orders</div>`; return; }

    let html = `<table class="ct-table"><thead><tr>
      <th>COIN</th><th>CURRENT PRICE</th><th>WAIT FOR (ENTRY)</th>
      <th>GAP TO ENTRY</th><th>TARGET</th><th>STOP-LOSS</th>
      <th>QUEUED AT</th><th>EXPIRES IN</th>
    </tr></thead><tbody>`;

    orders.forEach(o => {
        const mkt = symbolToMkt(o.symbol);
        const t   = snap.market[mkt];
        const cur = t ? t.last_price : null;
        const entry = parseFloat(o.entry_price || 0);
        const gap   = cur && entry ? ((entry - cur) / cur * 100) : null;
        const gapStr= gap !== null ? `${gap >= 0 ? "+" : ""}${gap.toFixed(2)}%` : "—";
        const gapCls= gap === null ? "" : (gap >= 0 ? "ct-gap-pos" : "ct-gap-neg");
        const createdAt = o.creation ? new Date(o.creation).toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit",second:"2-digit"}) : "—";
        const ageMs  = o.creation ? Date.now() - new Date(o.creation).getTime() : 0;
        const expMs  = 24*3600*1000 - ageMs;
        const expStr = expMs < 60000 ? "Expiring…" : fmtDuration(expMs);
        const expCls = expMs < 60000 ? "ct-expiring" : "";

        html += `<tr>
          <td><strong>${o.symbol}</strong></td>
          <td>${cur ? "₹"+fmt(cur) : "—"}</td>
          <td class="ct-accent">₹${fmt(entry)}</td>
          <td class="${gapCls}">${gapStr}</td>
          <td class="ct-buy">₹${fmt(o.target_price)}</td>
          <td class="ct-sell">₹${fmt(o.stop_loss)}</td>
          <td class="ct-muted-text">${createdAt}</td>
          <td class="${expCls}">${expStr}</td>
        </tr>`;
    });
    html += `</tbody></table>`;
    wrap.innerHTML = html;
}

/* ═ SCAN ═══════════════════════════════════════════════════════════ */

function runScan() {
    if (CT.scanInFlight) return;
    CT.scanInFlight = true;
    const btn = document.getElementById("btn-scan");
    btn.disabled = true; btn.textContent = "⟳ Scanning…";
    frappe.call({
        method: "coin_trader.scanner.run_scanner_api",
        callback(r) {
            CT.scanInFlight = false;
            btn.disabled = false; btn.textContent = "▶ Run Scan";
            const msg = r.message || {};
            if (msg.error) {
                frappe.show_alert({ message: msg.error, indicator: "orange" }, 5);
                setText("scan-summary", msg.error);
                return;
            }
            // scanner returns { signals: [...], symbol_count: N }
            const results = Array.isArray(msg) ? msg : (msg.signals || []);
            renderScan(results, msg.symbol_count);
            pollSnapshot();
        },
        error() {
            CT.scanInFlight = false;
            btn.disabled = false; btn.textContent = "▶ Run Scan";
        }
    });
}

function renderScan(results, symbolCount) {
    const wrap = document.getElementById("scan-body");
    const cnt = symbolCount !== undefined ? symbolCount : (results || []).length;
    setText("scan-summary", `${cnt} symbols scanned · ${(results||[]).length} signals`);
    if (!results.length) { wrap.innerHTML = `<div class="ct-empty">No results</div>`; return; }

    let html = `<div class="ct-scan-hd">
      <span>SYMBOL</span><span>SIGNAL</span><span>CONFIDENCE</span><span>REASON</span><span style="text-align:right">STATUS</span>
    </div>`;
    results.forEach(r => {
        const pred = r.ai_signal || r.prediction || "HOLD";
        const conf = parseFloat(r.ai_confidence || r.confidence_score || 0);
        const reason = r.reject_reason || r.approve_reason || r.reason || "—";
        const bg   = pred === "BUY" ? "ct-bg-buy" : pred === "SELL" ? "ct-bg-sell" : "ct-bg-hold";
        const cls  = conf >= 85 ? "ct-buy" : conf >= 70 ? "ct-accent" : "ct-muted-text";
        let status;
        if (r.executed)            status = `<span class="ct-badge ct-bg-live">EXECUTED</span>`;
        else if (r.risk_approved)  status = `<span class="ct-badge ct-bg-ok">APPROVED</span>`;
        else                       status = `<span class="ct-badge ct-bg-rej">REJECTED</span>`;
        html += `<div class="ct-scan-rw">
          <span class="ct-scan-sym">${r.symbol || "—"}</span>
          <span><span class="ct-badge ${bg}">${pred}</span></span>
          <span class="ct-scan-conf ${cls}">${conf.toFixed(0)}%</span>
          <span class="ct-scan-rzn" title="${escHtml(reason)}">${escHtml(reason)}</span>
          <span class="ct-scan-st">${status}</span>
        </div>`;
    });
    wrap.innerHTML = html;
}

/* ═ SIGNAL PROBE ══════════════════════════════════════════════════ */

function runProbe() {
    const symbol   = $("#probe-symbol").val();
    const interval = $("#probe-interval").val();
    if (!symbol) { frappe.show_alert({ message: "Select a symbol", indicator: "orange" }, 3); return; }
    $("#signal-body").html(`<div class="ct-loading">Fetching AI signal…</div>`);
    frappe.call({
        method: "coin_trader.ai_adapter.run_ai_validation_api",
        args: { symbol, interval },
        callback(r) {
            if (r.exc) { $("#signal-body").html(`<div class="ct-empty">Error fetching signal</div>`); return; }
            renderSignal(r.message || r);
        },
    });
}

function renderSignal(d) {
    const pred   = d.ai_signal || d.prediction || "HOLD";
    const conf   = parseFloat(d.ai_confidence || d.confidence_score || 0);
    const price  = parseFloat(d.close || 0);
    const sl     = parseFloat(d.ai_stop_loss || d.suggested_stoploss || 0);
    const tgt    = parseFloat(d.ai_target || d.suggested_target || 0);
    const atr    = parseFloat(d.atr || 0);
    const reason = d.ai_reason || d.reason || "";
    const flags  = d.risk_flags || [];

    const heroCls = pred === "BUY" ? "is-buy" : pred === "SELL" ? "is-sell" : "";
    const dirCls  = pred === "BUY" ? "ct-buy" : pred === "SELL" ? "ct-sell" : "ct-muted-text";
    const bg      = pred === "BUY" ? "ct-bg-buy" : pred === "SELL" ? "ct-bg-sell" : "ct-bg-hold";
    const confCls = conf >= 85 ? "ct-buy" : conf >= 70 ? "ct-accent" : "ct-sell";
    const rr      = sl > 0 && tgt > 0 && price > 0 ? (Math.abs(tgt-price)/Math.abs(price-sl)).toFixed(2) : "—";

    let html = `
      <div class="ct-sig-hero ${heroCls}">
        <div class="ct-sig-dir ${dirCls}">${pred}</div>
        <div class="ct-sig-bar">
          <div class="ct-sig-lbl">AI Confidence</div>
          <div class="ct-sig-track"><div class="ct-sig-fill" style="width:${conf}%"></div></div>
          <div class="ct-sig-val ${confCls}">${conf.toFixed(0)}%</div>
        </div>
        <span class="ct-badge ${bg}">${pred}</span>
      </div>
      <div class="ct-sig-meta">
        <div class="ct-mbox"><div class="ct-mlbl">Price</div><div class="ct-mval">₹${fmt(price)}</div></div>
        <div class="ct-mbox"><div class="ct-mlbl">ATR</div><div class="ct-mval ct-muted-text">${fmt(atr)}</div></div>
        <div class="ct-mbox"><div class="ct-mlbl">R : R</div><div class="ct-mval ct-accent">1 : ${rr}</div></div>
        <div class="ct-mbox"><div class="ct-mlbl">Stop-Loss</div><div class="ct-mval ct-sell">₹${fmt(sl)}</div></div>
        <div class="ct-mbox"><div class="ct-mlbl">Target</div><div class="ct-mval ct-buy">₹${fmt(tgt)}</div></div>
        <div class="ct-mbox"><div class="ct-mlbl">Min Conf</div><div class="ct-mval ${conf >= (CT.snapshot?.config.min_confidence || 80) ? "ct-buy" : "ct-sell"}">${CT.snapshot?.config.min_confidence || 80}%</div></div>
      </div>`;
    if (reason) html += `<div class="ct-ai-rzn">💬 ${escHtml(reason)}</div>`;
    if (flags.length) html += `<div class="ct-flags">${flags.map(f => `<span class="ct-flag">⚠ ${escHtml(f)}</span>`).join("")}</div>`;

    $("#signal-body").html(html);
}

/* ═ TRADE HISTORY ═════════════════════════════════════════════════ */

function loadTradeHistory() {
    frappe.call({
        method: "frappe.client.get_list",
        args: {
            doctype: "CT Trade Log",
            fields:  ["symbol","action","price","quantity","pnl_inr","pnl_pct","reason","is_paper_trade","trade_time"],
            filters: { user: frappe.session.user },
            limit:   40,
            order_by:"trade_time desc",
        },
        callback(r) { renderHistory(r.message || []); }
    });
}

function renderHistory(trades) {
    const wrap = document.getElementById("history-body");
    setText("hist-summary", `${trades.length} trades`);
    if (!trades.length) { wrap.innerHTML = `<div class="ct-empty">No trade history yet</div>`; return; }

    let total = 0;
    let html = `<table class="ct-table"><thead><tr>
      <th>SYMBOL</th><th>ACTION</th><th>PRICE</th><th>QTY</th>
      <th>PNL (INR)</th><th>PNL %</th><th>REASON</th><th>MODE</th><th>TIME</th>
    </tr></thead><tbody>`;
    trades.forEach(t => {
        const pnl  = parseFloat(t.pnl_inr || 0);
        const pnlP = parseFloat(t.pnl_pct || 0);
        const cls  = pnl > 0 ? "ct-buy" : pnl < 0 ? "ct-sell" : "ct-muted-text";
        const sign = pnl >= 0 ? "+" : "";
        total += pnl;
        const actCls = (t.action || "").toUpperCase() === "BUY" ? "ct-buy" : "ct-sell";
        const dt = t.trade_time ? new Date(t.trade_time).toLocaleString("en-IN",{dateStyle:"short",timeStyle:"short"}) : "—";
        const mode = t.is_paper_trade ? `<span class="ct-badge ct-bg-paper">PAPER</span>` : `<span class="ct-badge ct-bg-live">LIVE</span>`;
        html += `<tr>
          <td><strong>${t.symbol||"—"}</strong></td>
          <td class="${actCls}" style="font-weight:700">${(t.action||"").toUpperCase()}</td>
          <td>₹${fmt(t.price)}</td>
          <td>${fmtQty(t.quantity)}</td>
          <td class="${cls}" style="font-weight:700">${pnl !== 0 ? sign+"₹"+fmt(Math.abs(pnl)) : "—"}</td>
          <td class="${cls}">${pnlP !== 0 ? sign+pnlP.toFixed(2)+"%" : "—"}</td>
          <td class="ct-muted-text" style="max-width:240px;overflow:hidden;text-overflow:ellipsis" title="${escHtml(t.reason||"")}">${escHtml(t.reason||"—")}</td>
          <td>${mode}</td>
          <td class="ct-muted-text">${dt}</td>
        </tr>`;
    });
    html += `</tbody></table>`;
    const tcls = total >= 0 ? "ct-buy" : "ct-sell";
    html += `<div style="margin-top:10px;text-align:right;font-size:12px;color:var(--muted)">
      Total P&L shown: <strong class="${tcls}">${total >= 0 ? "+" : ""}₹${fmt(Math.abs(total))}</strong></div>`;
    wrap.innerHTML = html;
}

/* ═ BACKTEST ══════════════════════════════════════════════════════ */

function runBacktest() {
    const symbol   = $("#probe-symbol").val();
    const interval = $("#probe-interval").val();
    if (!symbol) { frappe.show_alert({ message: "Select a symbol first", indicator: "orange" }, 3); return; }

    $("#backtest-card").removeClass("hidden");
    $("#backtest-body").html(`<div class="ct-loading">Running walk-forward backtest…</div>`);
    document.getElementById("backtest-card").scrollIntoView({ behavior: "smooth", block: "start" });

    frappe.call({
        method: "coin_trader.backtest_engine.run_backtest_api",
        args: { symbol, interval, limit: 1000, min_confidence: CT.snapshot?.config.min_confidence || 80 },
        callback(r) {
            const res = r.message || {};
            if (!res.success) { $("#backtest-body").html(`<div class="ct-empty">${escHtml(res.error || "Backtest failed")}</div>`); return; }
            renderBacktest(res, symbol, interval);
        }
    });
}

function renderBacktest(res, symbol, interval) {
    const m = res.metrics || {};
    const tr= res.trades  || [];
    const wc = (m.win_rate||0) >= 55 ? "ct-buy" : "ct-sell";
    const pc = (m.profit_factor||0) >= 1.5 ? "ct-buy" : "ct-sell";
    const nc = (m.net_profit_pct||0) >= 0 ? "ct-buy" : "ct-sell";
    const dc = (m.max_drawdown_pct||0) <= 15 ? "ct-buy" : "ct-sell";
    const sc = (m.sharpe_ratio||0) >= 1 ? "ct-buy" : "ct-sell";

    let html = `<div style="margin-bottom:14px;font-weight:700;font-size:14px">${symbol} · ${interval} · Walk-Forward</div>
      <div class="ct-bt-grid">
        <div class="ct-bt-stat"><div class="ct-bt-lbl">Trades</div><div class="ct-bt-val">${m.total_trades||0}</div></div>
        <div class="ct-bt-stat"><div class="ct-bt-lbl">Win Rate</div><div class="ct-bt-val ${wc}">${(m.win_rate||0).toFixed(1)}%</div></div>
        <div class="ct-bt-stat"><div class="ct-bt-lbl">Profit Factor</div><div class="ct-bt-val ${pc}">${(m.profit_factor||0).toFixed(2)}x</div></div>
        <div class="ct-bt-stat"><div class="ct-bt-lbl">Net Profit</div><div class="ct-bt-val ${nc}">${(m.net_profit_pct||0) >= 0 ? "+" : ""}${(m.net_profit_pct||0).toFixed(2)}%</div></div>
        <div class="ct-bt-stat"><div class="ct-bt-lbl">Max Drawdown</div><div class="ct-bt-val ${dc}">${(m.max_drawdown_pct||0).toFixed(2)}%</div></div>
        <div class="ct-bt-stat"><div class="ct-bt-lbl">Sharpe</div><div class="ct-bt-val ${sc}">${(m.sharpe_ratio||0).toFixed(2)}</div></div>
        <div class="ct-bt-stat"><div class="ct-bt-lbl">Avg Win</div><div class="ct-bt-val ct-buy">+${(m.avg_win_pct||0).toFixed(2)}%</div></div>
        <div class="ct-bt-stat"><div class="ct-bt-lbl">Avg Loss</div><div class="ct-bt-val ct-sell">${(m.avg_loss_pct||0).toFixed(2)}%</div></div>
        <div class="ct-bt-stat"><div class="ct-bt-lbl">Expectancy</div><div class="ct-bt-val">${(m.expectancy_pct||0).toFixed(3)}%</div></div>
        <div class="ct-bt-stat"><div class="ct-bt-lbl">Avg Hold</div><div class="ct-bt-val">${(m.avg_duration_candles||0).toFixed(1)}c</div></div>
      </div>`;

    if (tr.length) {
        html += `<div style="font-size:12px;color:var(--muted);margin-bottom:8px">Sample trades (up to 50)</div>
          <table class="ct-table"><thead><tr>
            <th>#</th><th>DIR</th><th>ENTRY</th><th>EXIT</th><th>PNL %</th><th>EXIT REASON</th><th>HOLD</th>
          </tr></thead><tbody>`;
        tr.slice(0, 50).forEach((t,i) => {
            const c = (t.pnl_pct||0) >= 0 ? "ct-buy" : "ct-sell";
            const s = (t.pnl_pct||0) >= 0 ? "+" : "";
            const dc = (t.prediction||"").toUpperCase() === "BUY" ? "ct-buy" : "ct-sell";
            html += `<tr>
              <td class="ct-muted-text">${i+1}</td>
              <td class="${dc}" style="font-weight:700">${t.prediction||"—"}</td>
              <td>₹${fmt(t.entry_price)}</td><td>₹${fmt(t.exit_price)}</td>
              <td class="${c}" style="font-weight:700">${s}${(t.pnl_pct||0).toFixed(2)}%</td>
              <td class="ct-muted-text">${t.exit_reason||"—"}</td>
              <td class="ct-muted-text">${t.duration_candles||0}</td>
            </tr>`;
        });
        html += `</tbody></table>`;
    }
    $("#backtest-body").html(html);
}

/* ═ CLOSE POSITION ═══════════════════════════════════════════════ */

window.CT_DASH = {
    close_position(name, symbol) {
        frappe.confirm(`Close position for <b>${symbol}</b> at market price?`, () => {
            frappe.call({
                method: "coin_trader.trader.manual_close_position",
                args: { position_name: name },
                callback(r) {
                    if (r.message && r.message.success) {
                        frappe.show_alert({ message: `${symbol} closed`, indicator: "green" }, 3);
                        pollSnapshot(); loadTradeHistory();
                    } else {
                        frappe.msgprint(r.message && r.message.message ? r.message.message : "Close failed");
                    }
                }
            });
        });
    }
};

/* ═ HELPERS ════════════════════════════════════════════════════════ */

function setText(id, text) { const el = document.getElementById(id); if (el) el.textContent = text; }

// CoinDCX ticker uses FLAT keys ("BTCINR"). Snapshot market keys are already flat.
// CT_DEFAULT_SYMBOLS are already flat keys. Tracked symbols from config have prefix/underscore.
function symbolToMkt(sym) {
    if (!sym) return "";
    // Already a flat key (no dash, no underscore) → return as-is
    if (!sym.includes("-") && !sym.includes("_")) return sym.toUpperCase();
    return sym.toUpperCase().replace(/^[A-Z]-/, "").replace(/_/g, "");
}
function mktToSymbol(mkt, list) {
    return (list || []).find(s => symbolToMkt(s) === mkt) || mkt;
}
function shortSym(mkt) {
    return mkt.replace(/^[A-Z]-/, "").replace(/_/g, "");
}

function fmt(n) {
    const v = parseFloat(n);
    if (isNaN(v)) return "—";
    if (v >= 100000) return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
    if (v >= 1)      return v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
    if (v >= 0.01)   return v.toFixed(4);
    return v.toFixed(6);
}
function fmtINR(n) {
    const v = parseFloat(n);
    if (isNaN(v)) return "0";
    return v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}
function fmtVol(n) {
    const v = parseFloat(n);
    if (isNaN(v)) return "—";
    if (v >= 1e7) return (v/1e7).toFixed(2) + "Cr";
    if (v >= 1e5) return (v/1e5).toFixed(2) + "L";
    if (v >= 1e3) return (v/1e3).toFixed(1) + "K";
    return v.toFixed(2);
}
function fmtQty(n) {
    const v = parseFloat(n);
    if (isNaN(v)) return "—";
    if (v >= 1)    return v.toFixed(4);
    return v.toFixed(6);
}
function fmtDuration(ms) {
    const h = Math.floor(ms/3.6e6);
    const m = Math.floor((ms%3.6e6)/60000);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
}
function escHtml(s) {
    return String(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

/**
 * Coin Trader Dashboard
 * Handles: ML signal probe, position table, scan results,
 *          trade history, backtest panel, realtime updates.
 */

(function () {
    "use strict";

    // ── Helpers ────────────────────────────────────────────────────────────

    function call(method, args) {
        return new Promise(function (resolve, reject) {
            frappe.call({
                method: method,
                args: args || {},
                callback: function (r) {
                    if (r && r.message !== undefined) resolve(r.message);
                    else reject(r);
                },
                error: reject,
            });
        });
    }

    function fmt_inr(v) {
        v = parseFloat(v) || 0;
        return "₹" + v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
    }

    function fmt_pct(v) {
        v = parseFloat(v) || 0;
        return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
    }

    function signal_class(s) {
        if (s === "BUY")  return "ct-buy";
        if (s === "SELL") return "ct-sell";
        return "ct-hold";
    }

    function quality_badge(q) {
        var map = { Strong: "ct-badge-success", Moderate: "ct-badge-info",
                    Weak: "ct-badge-warn", Noise: "ct-badge-danger" };
        return '<span class="ct-badge ' + (map[q] || "") + '">' + (q || "—") + "</span>";
    }

    function set_html(id, html) {
        var el = document.getElementById(id);
        if (el) el.innerHTML = html;
    }

    function show(id) { var e = document.getElementById(id); if (e) e.classList.remove("hidden"); }
    function hide(id) { var e = document.getElementById(id); if (e) e.classList.add("hidden"); }

    // ── Symbol / interval ─────────────────────────────────────────────────

    function get_symbol() {
        return document.getElementById("symbol-select").value;
    }
    function get_interval() {
        return document.getElementById("interval-select").value;
    }

    // ── ML Signal probe ───────────────────────────────────────────────────

    function render_probability(data) {
        if (data.error) {
            set_html("prob-body", '<div class="ct-error">' + data.error + "</div>");
            return;
        }

        var pred       = data.ai_signal || data.prediction || "—";
        var conf       = data.ai_confidence || data.confidence_score || 0;
        var up         = parseFloat(data.upward_probability || 0).toFixed(1);
        var down       = parseFloat(data.downward_probability || 0).toFixed(1);
        var quality    = data.signal_quality || "—";
        var close      = parseFloat(data.close || 0);
        var atr        = parseFloat(data.atr || 0);
        var sl         = parseFloat(data.ai_stop_loss || data.suggested_stoploss || 0);
        var tgt        = parseFloat(data.ai_target || data.suggested_target || 0);
        var ai_reason  = data.ai_reason || "";
        var ai_flags   = data.ai_risk_flags || [];
        var call_ai    = data.call_ai;
        var ai_valid   = data.ai_validated;

        var flags_html = "";
        if (ai_flags.length) {
            flags_html = '<div class="ct-risk-flags"><strong>Risk flags:</strong> '
                + ai_flags.map(function(f){ return '<span class="ct-flag">⚠ ' + f + "</span>"; }).join(" ")
                + "</div>";
        }

        var ai_badge = ai_valid
            ? '<span class="ct-badge ct-badge-success">AI Validated</span>'
            : (call_ai ? '<span class="ct-badge ct-badge-warn">AI Skipped</span>'
                       : '<span class="ct-badge">ML Only</span>');

        set_html("prob-body",
            '<div class="ct-signal-main ' + signal_class(pred) + '">'
            +   '<span class="ct-signal-label">' + pred + "</span>"
            +   '<span class="ct-conf-bar-wrap"><div class="ct-conf-bar" style="width:' + conf + '%"></div></span>'
            +   '<span class="ct-conf-value">' + conf + "%</span>"
            + "</div>"
            + '<div class="ct-signal-row">'
            +   quality_badge(quality) + " " + ai_badge
            + "</div>"
            + '<div class="ct-signal-probs">'
            +   '<span class="ct-buy">▲ ' + up + "%</span>"
            +   ' <span class="ct-sell">▼ ' + down + "%</span>"
            + "</div>"
            + '<table class="ct-levels-table">'
            +   "<tr><td>Price</td><td>" + fmt_inr(close) + "</td></tr>"
            +   "<tr><td>ATR</td><td>" + fmt_inr(atr) + "</td></tr>"
            +   '<tr><td>Stop Loss</td><td class="ct-sell">' + fmt_inr(sl) + "</td></tr>"
            +   '<tr><td>Target</td><td class="ct-buy">' + fmt_inr(tgt) + "</td></tr>"
            + "</table>"
            + (ai_reason ? '<div class="ct-ai-reason">' + ai_reason + "</div>" : "")
            + flags_html
        );
    }

    function probe_signal() {
        var sym = get_symbol();
        var iv  = get_interval();
        if (!sym) return;

        show("prob-spinner");
        set_html("prob-body", '<div class="ct-loading">Fetching signal for ' + sym + "…</div>");

        call("coin_trader.ai_adapter.run_ai_validation_api", { symbol: sym, interval: iv })
            .then(function (data) {
                hide("prob-spinner");
                render_probability(data);
            })
            .catch(function () {
                hide("prob-spinner");
                set_html("prob-body", '<div class="ct-error">Failed to fetch signal.</div>');
            });
    }

    // ── Open positions ────────────────────────────────────────────────────

    function render_positions(rows) {
        if (!rows || !rows.length) {
            set_html("positions-table-wrap", '<div class="ct-empty-state">No open positions.</div>');
            document.getElementById("hdr-open-count").textContent = "0";
            return;
        }
        document.getElementById("hdr-open-count").textContent = rows.length;

        var html = '<table class="ct-table"><thead><tr>'
            + "<th>Symbol</th><th>Entry</th><th>SL</th>"
            + "<th>Target</th><th>Qty</th><th>PnL</th><th>Since</th><th></th>"
            + "</tr></thead><tbody>";

        rows.forEach(function (p) {
            html += "<tr>"
                + "<td><strong>" + p.symbol + "</strong></td>"
                + "<td>" + fmt_inr(p.buy_price) + "</td>"
                + '<td class="ct-sell">' + fmt_inr(p.stop_loss) + "</td>"
                + '<td class="ct-buy">' + fmt_inr(p.target_price) + "</td>"
                + "<td>" + parseFloat(p.quantity || 0).toFixed(6) + "</td>"
                + '<td class="' + (parseFloat(p.pnl_inr||0) >= 0 ? "ct-pos" : "ct-neg") + '">'
                + fmt_inr(p.pnl_inr || 0) + "</td>"
                + "<td>" + (p.entry_time ? p.entry_time.substring(0, 16) : "—") + "</td>"
                + '<td><button class="ct-btn ct-btn-xs ct-btn-danger" data-name="'
                + p.name + '" onclick="CT_DASH.close_position(this)">Close</button></td>'
                + "</tr>";
        });

        html += "</tbody></table>";
        set_html("positions-table-wrap", html);
    }

    function load_positions() {
        call("coin_trader.trader.get_open_positions")
            .then(render_positions)
            .catch(function () {
                set_html("positions-table-wrap", '<div class="ct-error">Failed to load positions.</div>');
            });
    }

    function close_position(btn) {
        var name = btn.getAttribute("data-name");
        if (!confirm("Close position " + name + " at market price?")) return;
        btn.disabled = true;
        btn.textContent = "…";
        call("coin_trader.trader.manual_close_position", { position_name: name })
            .then(function (r) {
                if (r && r.success) {
                    frappe.show_alert({ message: "Position closed at " + fmt_inr(r.exit_price), indicator: "green" });
                    load_positions();
                    load_trade_history();
                } else {
                    frappe.show_alert({ message: (r && r.message) || "Close failed", indicator: "red" });
                    btn.disabled = false;
                    btn.textContent = "Close";
                }
            });
    }

    // ── Scanner ───────────────────────────────────────────────────────────

    function run_scan() {
        var btn  = document.getElementById("btn-scan");
        var text = document.getElementById("btn-scan-text");
        btn.disabled = true;
        text.textContent = "Scanning…";
        set_html("scan-results-wrap", '<div class="ct-loading">Running scan across all symbols…</div>');

        call("coin_trader.scanner.run_scanner_api")
            .then(function (data) {
                btn.disabled = false;
                text.textContent = "Run Scan";
                render_scan_results(data);
            })
            .catch(function () {
                btn.disabled = false;
                text.textContent = "Run Scan";
                set_html("scan-results-wrap", '<div class="ct-error">Scan failed. Check error log.</div>');
            });
    }

    function render_scan_results(data) {
        if (data && data.error) {
            set_html("scan-results-wrap", '<div class="ct-error">' + data.error + "</div>");
            return;
        }

        var signals = (data && data.signals) || [];
        var ts      = (data && data.scanned_at) ? data.scanned_at.substring(0, 16) : "—";
        document.getElementById("last-scan-time").textContent = "Last scan: " + ts;

        var cnt_el = document.getElementById("scan-symbol-count");
        cnt_el.textContent = signals.length + " signals";

        if (!signals.length) {
            set_html("scan-results-wrap", '<div class="ct-empty-state">No actionable signals this scan.</div>');
            return;
        }

        var html = '<table class="ct-table"><thead><tr>'
            + "<th>Symbol</th><th>Signal</th><th>Confidence</th>"
            + "<th>Quality</th><th>AI</th><th>Executed</th><th>Reason</th>"
            + "</tr></thead><tbody>";

        signals.forEach(function (s) {
            var pred_cls = signal_class(s.prediction);
            html += "<tr>"
                + "<td><strong>" + s.symbol + "</strong></td>"
                + '<td class="' + pred_cls + '"><strong>' + (s.prediction || "—") + "</strong></td>"
                + "<td>" + (s.confidence || 0) + "%</td>"
                + "<td>" + quality_badge(s.signal_quality) + "</td>"
                + "<td>" + (s.ai_validated ? "✓" : "—") + "</td>"
                + "<td>" + (s.executed ? '<span class="ct-badge ct-badge-success">✓</span>'
                                       : '<span class="ct-badge">—</span>') + "</td>"
                + "<td class='ct-reason'>" + (s.reject_reason || "") + "</td>"
                + "</tr>";
        });

        html += "</tbody></table>";
        set_html("scan-results-wrap", html);
    }

    // ── Trade history ─────────────────────────────────────────────────────

    function load_trade_history() {
        call("frappe.client.get_list", {
            doctype: "CT Trade Log",
            filters: [["user", "=", frappe.session.user]],
            fields: ["name", "symbol", "action", "quantity",
                     "price", "pnl_inr", "pnl_pct", "reason", "is_paper_trade", "trade_time"],
            order_by: "trade_time desc",
            limit: 20,
        }).then(function (rows) {
            if (!rows || !rows.length) {
                set_html("trade-history-wrap", '<div class="ct-empty-state">No closed trades yet.</div>');
                return;
            }

            var total_pnl = 0;
            var html = '<table class="ct-table"><thead><tr>'
                + "<th>Symbol</th><th>Action</th><th>Price</th><th>Qty</th>"
                + "<th>PnL</th><th>PnL%</th><th>Reason</th><th>Paper</th><th>Time</th>"
                + "</tr></thead><tbody>";

            rows.forEach(function (t) {
                var pnl     = parseFloat(t.pnl_inr || 0);
                var pnl_cls = pnl >= 0 ? "ct-pos" : "ct-neg";
                total_pnl  += pnl;
                html += "<tr>"
                    + "<td><strong>" + t.symbol + "</strong></td>"
                    + '<td class="' + signal_class(t.action) + '">' + (t.action || "—") + "</td>"
                    + "<td>" + fmt_inr(t.price) + "</td>"
                    + "<td>" + parseFloat(t.quantity || 0).toFixed(6) + "</td>"
                    + '<td class="' + pnl_cls + '"><strong>' + fmt_inr(pnl) + "</strong></td>"
                    + '<td class="' + pnl_cls + '">' + fmt_pct(t.pnl_pct || 0) + "</td>"
                    + "<td>" + (t.reason || "—") + "</td>"
                    + "<td>" + (t.is_paper_trade ? "✓" : "") + "</td>"
                    + "<td>" + (t.trade_time ? t.trade_time.substring(0, 16) : "—") + "</td>"
                    + "</tr>";
            });

            html += "</tbody></table>";
            html += '<div class="ct-history-footer">'
                + 'Showing last ' + rows.length + ' trades. '
                + 'Total PnL: <strong class="' + (total_pnl >= 0 ? "ct-pos" : "ct-neg") + '">'
                + fmt_inr(total_pnl) + "</strong></div>";

            set_html("trade-history-wrap", html);

            // Update header PnL
            document.getElementById("hdr-today-pnl").textContent = fmt_inr(total_pnl);
        }).catch(function () {
            set_html("trade-history-wrap", '<div class="ct-error">Failed to load trade history.</div>');
        });
    }

    // ── Backtest ──────────────────────────────────────────────────────────

    function run_backtest() {
        var sym = get_symbol();
        var iv  = get_interval();
        if (!sym) { frappe.show_alert({ message: "Select a symbol first", indicator: "orange" }); return; }

        show("card-backtest");
        set_html("backtest-body", '<div class="ct-loading">Running backtest for ' + sym + ' ' + iv + '…</div>');

        call("coin_trader.backtest_engine.run_backtest_api", {
            symbol: sym,
            interval: iv,
            limit: 1000,
            min_confidence: window._CT.min_conf,
        }).then(function (data) {
            render_backtest(data, sym, iv);
        }).catch(function () {
            set_html("backtest-body", '<div class="ct-error">Backtest failed. Check error log.</div>');
        });
    }

    function render_backtest(data, sym, iv) {
        if (!data || !data.success) {
            set_html("backtest-body", '<div class="ct-error">' + (data && data.error || "Backtest failed") + "</div>");
            return;
        }

        var m = data.metrics;
        var trades = data.trades || [];

        var pnl_cls = parseFloat(m.net_profit_pct) >= 0 ? "ct-pos" : "ct-neg";

        var html = '<div class="ct-bt-summary">'
            + '<h3>' + sym + ' · ' + iv + ' · ' + (data.backtest_name || "") + '</h3>'
            + '<div class="ct-bt-grid">'
            + bt_stat("Total Trades", m.total_trades)
            + bt_stat("Win Rate", m.win_rate + "%")
            + bt_stat("Profit Factor", m.profit_factor)
            + bt_stat("Net Profit", '<span class="' + pnl_cls + '">' + fmt_pct(m.net_profit_pct) + "</span>")
            + bt_stat("Max Drawdown", m.max_drawdown_pct + "%")
            + bt_stat("Sharpe Ratio", m.sharpe_ratio)
            + bt_stat("Avg Win", fmt_pct(m.avg_win_pct))
            + bt_stat("Avg Loss", fmt_pct(m.avg_loss_pct))
            + bt_stat("Expectancy", fmt_pct(m.expectancy_pct))
            + bt_stat("Avg Hold", m.avg_duration_candles + " candles")
            + "</div></div>";

        if (trades.length) {
            html += '<div class="ct-bt-trades"><h4>Sample Trades (first ' + trades.length + ')</h4>'
                + '<table class="ct-table"><thead><tr>'
                + "<th>#</th><th>Dir</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Reason</th><th>Hold</th>"
                + "</tr></thead><tbody>";

            trades.forEach(function (t, i) {
                var pnl_cls = parseFloat(t.pnl_pct) >= 0 ? "ct-pos" : "ct-neg";
                html += "<tr>"
                    + "<td>" + (i + 1) + "</td>"
                    + '<td class="' + signal_class(t.direction) + '">' + t.direction + "</td>"
                    + "<td>" + fmt_inr(t.entry_price) + "</td>"
                    + "<td>" + fmt_inr(t.exit_price) + "</td>"
                    + '<td class="' + pnl_cls + '">' + fmt_pct(t.pnl_pct) + "</td>"
                    + "<td>" + (t.exit_reason || "—") + "</td>"
                    + "<td>" + (t.duration || 0) + "</td>"
                    + "</tr>";
            });

            html += "</tbody></table></div>";
        }

        set_html("backtest-body", html);
    }

    function bt_stat(label, value) {
        return '<div class="ct-bt-stat"><div class="ct-bt-label">' + label
            + '</div><div class="ct-bt-value">' + value + "</div></div>";
    }

    // ── Realtime events ───────────────────────────────────────────────────

    function setup_realtime() {
        frappe.realtime.on("ct_scan_complete", function (data) {
            render_scan_results(data);
            load_positions();
            frappe.show_alert({ message: "Scan complete — " + (data.symbol_count || 0) + " symbols", indicator: "blue" });
        });

        frappe.realtime.on("ct_position_opened", function (data) {
            load_positions();
            frappe.show_alert({
                message: "Position opened: " + data.direction + " " + data.symbol
                       + " @ " + fmt_inr(data.price),
                indicator: data.direction === "BUY" ? "green" : "red",
            });
        });

        frappe.realtime.on("ct_position_closed", function (data) {
            load_positions();
            load_trade_history();
            var pnl = parseFloat(data.pnl || 0);
            frappe.show_alert({
                message: data.symbol + " closed (" + data.exit_reason + ") PnL: " + fmt_inr(pnl),
                indicator: pnl >= 0 ? "green" : "red",
            });
        });
    }

    // ── Auto-refresh positions every 60 s ─────────────────────────────────

    function start_auto_refresh() {
        setInterval(function () {
            load_positions();
        }, 60 * 1000);
    }

    // ── Init ──────────────────────────────────────────────────────────────

    function init() {
        document.getElementById("btn-probe").addEventListener("click", probe_signal);
        document.getElementById("btn-scan").addEventListener("click", run_scan);
        document.getElementById("btn-refresh-pos").addEventListener("click", load_positions);
        document.getElementById("btn-backtest").addEventListener("click", run_backtest);
        document.getElementById("btn-close-bt").addEventListener("click", function () {
            hide("card-backtest");
        });

        load_positions();
        load_trade_history();
        setup_realtime();
        start_auto_refresh();
    }

    // Expose close_position globally (called from inline onclick)
    window.CT_DASH = { close_position: close_position };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

})();

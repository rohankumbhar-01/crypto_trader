const $ = (id) => document.getElementById(id);
const API_METHOD = "coin_trader.chrome_extension_api";
let settings = null;
let snapshot = null;

function money(value) {
  const n = Number(value || 0);
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(n);
}
function showMessage(text, error = false) {
  const el = $("message");
  el.textContent = text;
  el.classList.toggle("error", error);
  el.hidden = !text;
}
function setConnected(connected) {
  $("connectionDot").classList.toggle("online", connected);
  $("activeBadge").textContent = connected ? "CONNECTED" : "OFFLINE";
  $("activeBadge").classList.toggle("muted", !connected);
}
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
}
async function loadSettings() {
  const data = await chrome.storage.local.get(["siteUrl", "apiToken"]);
  settings = { siteUrl: (data.siteUrl || "").replace(/\/$/, ""), apiToken: data.apiToken || "" };
  $("siteLabel").textContent = settings.siteUrl ? new URL(settings.siteUrl).host : "No site configured";
}
async function api(method, args = {}) {
  if (!settings?.siteUrl || !settings?.apiToken) throw new Error("Open Connection settings and configure your Frappe site and API token.");
  const response = await fetch(`${settings.siteUrl}/api/method/${API_METHOD}.${method}`, {
    method: "POST",
    headers: { "Authorization": `token ${settings.apiToken}`, "Content-Type": "application/json", "Accept": "application/json" },
    body: JSON.stringify(args)
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.exc) throw new Error(data._server_messages || data.message || `Request failed (${response.status})`);
  return data.message ?? data;
}
function render(snapshotData) {
  snapshot = snapshotData;
  const cfg = snapshotData.config || {};
  const stats = snapshotData.stats || {};
  const paper = Boolean(cfg.dry_run);
  $("modeValue").textContent = cfg.is_active ? (paper ? "Paper trading" : "LIVE TRADING") : "Engine inactive";
  $("activeBadge").textContent = cfg.is_active ? (paper ? "SIMULATION" : "LIVE") : "INACTIVE";
  $("activeBadge").classList.toggle("live", cfg.is_active && !paper);
  $("activeBadge").classList.toggle("muted", !cfg.is_active);
  $("openPositions").textContent = stats.open_positions ?? 0;
  $("todayTrades").textContent = stats.today_trades ?? 0;
  $("todayPnl").textContent = money(stats.today_pnl_inr);
  $("todayPnl").className = Number(stats.today_pnl_inr || 0) >= 0 ? "positive" : "negative";
  $("winRate").textContent = stats.win_rate_pct == null ? "—" : `${Number(stats.win_rate_pct).toFixed(1)}%`;
  $("scannerStatus").textContent = cfg.is_active ? "Configured" : "Inactive";
  $("symbolCount").textContent = stats.enabled_symbols ?? "—";
  $("riskPerTrade").textContent = cfg.risk_per_trade_pct == null ? "—" : `${cfg.risk_per_trade_pct}%`;
  $("dailyLimit").textContent = cfg.max_daily_loss_inr == null ? "—" : money(cfg.max_daily_loss_inr);
  $("statusText").textContent = snapshotData.message || "Connected to your Coin Trader backend.";
  $("paperModeBtn").disabled = !cfg.name || paper;
  $("scanBtn").disabled = !cfg.name || !cfg.is_active || !paper;
  const trades = snapshotData.recent_trades || [];
  $("tradeList").innerHTML = trades.length ? trades.slice(0,5).map(t => {
    const pnl = Number(t.pnl_inr || 0);
    return `<div class="trade"><div class="trade-main">${esc(t.action || "TRADE")} · ${esc(t.symbol || "—")}</div><div class="trade-pnl ${pnl >= 0 ? "positive" : "negative"}">${money(pnl)}</div><div class="trade-meta">${esc(t.trade_time || "Time unavailable")} · ${t.is_paper_trade ? "Paper" : "Live"}</div><div class="trade-meta">${esc(t.reason || "")}</div></div>`;
  }).join("") : '<div class="empty">No recent trades found.</div>';
}
async function refresh() {
  $("refreshBtn").disabled = true;
  try {
    await loadSettings();
    const data = await api("get_dashboard_snapshot");
    render(data);
    setConnected(true);
    showMessage("");
  } catch (error) {
    setConnected(false);
    $("statusText").textContent = error.message;
    $("paperModeBtn").disabled = true;
    $("scanBtn").disabled = true;
    showMessage(error.message, true);
  } finally {
    $("refreshBtn").disabled = false;
  }
}
$("refreshBtn").addEventListener("click", refresh);
$("settingsBtn").addEventListener("click", () => chrome.runtime.openOptionsPage());
$("paperModeBtn").addEventListener("click", async () => {
  if (!confirm("Switch your active Coin Trader configuration to paper mode? This will prevent new live orders from this configuration.")) return;
  try {
    await api("force_paper_mode");
    showMessage("Paper mode enabled. Refreshing status…");
    await refresh();
  } catch (error) { showMessage(error.message, true); }
});
$("scanBtn").addEventListener("click", async () => {
  if (!confirm("Run one scan in paper mode? No live orders should be placed.")) return;
  $("scanBtn").disabled = true;
  try {
    const result = await api("run_paper_scan");
    showMessage(result.message || "Paper scan completed.");
    await refresh();
  } catch (error) { showMessage(error.message, true); }
  finally { $("scanBtn").disabled = !(snapshot?.config?.is_active && snapshot?.config?.dry_run); }
});
refresh();
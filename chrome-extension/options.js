const $ = id => document.getElementById(id);
const API_METHOD = "coin_trader.chrome_extension_api";
function showMessage(text, error = false) {
  const el = $("message"); el.textContent = text; el.hidden = !text; el.classList.toggle("error", error);
}
async function requestPermission(siteUrl) {
  const url = new URL(siteUrl);
  const origin = `${url.origin}/*`;
  const granted = await chrome.permissions.request({ origins: [origin] });
  if (!granted) throw new Error("Chrome permission was not granted for this site.");
}
async function testConnection(siteUrl, apiToken) {
  const response = await fetch(`${siteUrl}/api/method/${API_METHOD}.get_dashboard_snapshot`, {
    method: "POST",
    headers: { "Authorization": `token ${apiToken}`, "Content-Type": "application/json", "Accept": "application/json" },
    body: "{}"
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.exc) throw new Error(data._server_messages || data.message || `Connection failed (${response.status})`);
  return data.message ?? data;
}
$("settingsForm").addEventListener("submit", async event => {
  event.preventDefault();
  const siteUrl = $("siteUrl").value.trim().replace(/\/$/, "");
  const apiToken = $("apiToken").value.trim();
  try {
    const parsed = new URL(siteUrl);
    if (!["https:", "http:"].includes(parsed.protocol)) throw new Error("Use an HTTP(S) site URL.");
    if (parsed.protocol === "http:" && !["localhost", "127.0.0.1"].includes(parsed.hostname)) throw new Error("Use HTTPS for non-local sites.");
    await requestPermission(siteUrl);
    await testConnection(siteUrl, apiToken);
    await chrome.storage.local.set({ siteUrl, apiToken });
    showMessage("Connected successfully. You can close this tab and open the extension popup.");
  } catch (error) { showMessage(error.message, true); }
});
$("clearBtn").addEventListener("click", async () => {
  await chrome.storage.local.remove(["siteUrl", "apiToken"]);
  $("siteUrl").value = ""; $("apiToken").value = "";
  showMessage("Saved connection settings cleared.");
});
(async () => {
  const data = await chrome.storage.local.get(["siteUrl", "apiToken"]);
  $("siteUrl").value = data.siteUrl || "";
  $("apiToken").value = data.apiToken || "";
})();
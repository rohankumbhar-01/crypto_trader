import secrets
import frappe
from frappe import _
from frappe.model.document import Document


class CTWebhookConfig(Document):
    def before_insert(self):
        if not self.secret_token:
            from frappe.utils.password import update_password
            token = secrets.token_hex(24)
            self.secret_token = token

    def on_update(self):
        self.inbound_info = (
            "POST /api/method/coin_trader.webhook_handler.receive\n"
            "Headers: X-CT-Token: <secret_token>\n"
            "Body: {\"symbol\": \"BTCINR\", \"signal\": \"BUY\", \"confidence\": 85, \"user\": \"" + (self.user or "") + "\"}"
        )


@frappe.whitelist()
def test_outbound_webhook(doc_name: str) -> dict:
    """Send a test payload to the configured outbound URL."""
    doc = frappe.get_doc("CT Webhook Config", doc_name)
    if not doc.outbound_url:
        return {"success": False, "message": "No outbound URL configured."}

    from coin_trader.outbound_webhook import _post_outbound
    payload = {
        "event":      "test",
        "user":       doc.user,
        "message":    "Coin Trader webhook test — connection successful",
        "timestamp":  frappe.utils.now_datetime().isoformat(),
        "app":        "Coin Trader",
    }
    ok, status = _post_outbound(doc.outbound_url, payload)
    result_msg = f"✅ Delivered ({status})" if ok else f"❌ Failed ({status})"

    frappe.db.set_value("CT Webhook Config", doc_name, {
        "last_outbound_at":     frappe.utils.now_datetime(),
        "last_outbound_status": result_msg,
    })
    return {"success": ok, "message": result_msg}

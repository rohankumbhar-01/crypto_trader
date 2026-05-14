import frappe
import requests
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class CTNotificationConfig(Document):
	def validate(self):
		self._validate_channel_fields()

	def _validate_channel_fields(self):
		if self.channel in ("Telegram", "Both"):
			if not self.bot_token or not self.chat_id:
				frappe.throw(_("Bot Token and Chat ID are required for Telegram notifications."))
		if self.channel in ("Email", "Both"):
			if not self.email:
				frappe.throw(_("Email address is required for Email notifications."))


@frappe.whitelist()
def send_test_notification(doc_name: str) -> dict:
	"""Send a test notification via all configured channels for this config."""
	doc = frappe.get_doc("CT Notification Config", doc_name)
	if doc.user != frappe.session.user and not frappe.has_permission("CT Notification Config", "write"):
		frappe.throw(_("Not permitted"))

	channel  = doc.channel or "Telegram"
	ts       = now_datetime().strftime("%d %b %Y %H:%M:%S")
	results  = {}

	text = (
		f"✅ <b>Coin Trader — Test Notification</b>\n"
		f"━━━━━━━━━━━━━━━━━━━━\n"
		f"<b>Status  :</b> Connected successfully!\n"
		f"<b>Channel :</b> {channel}\n"
		f"<b>User    :</b> {doc.user}\n"
		f"<b>Time    :</b> {ts}\n\n"
		f"You will receive alerts for:\n"
		f"🟢 Position opened\n"
		f"🛑 Stop-loss hit\n"
		f"🎯 Target reached\n"
		f"🔎 Scan signals\n"
		f"📊 Daily P&amp;L summary"
	)

	if channel in ("Telegram", "Both"):
		try:
			bot_token = doc.get_password("bot_token")
			chat_id   = doc.chat_id
			url  = f"https://api.telegram.org/bot{bot_token}/sendMessage"
			resp = requests.post(
				url,
				json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
				timeout=8,
			)
			if resp.ok:
				results["telegram"] = "✅ Sent successfully"
			else:
				err = resp.json().get("description", resp.text[:100])
				results["telegram"] = f"❌ Failed: {err}"
		except Exception as e:
			results["telegram"] = f"❌ Error: {str(e)[:100]}"

	if channel in ("Email", "Both"):
		try:
			frappe.sendmail(
				recipients=[doc.email],
				subject="✅ Coin Trader — Test Notification",
				message=f"<pre style='font-family:monospace'>{text.replace('<b>','<strong>').replace('</b>','</strong>')}</pre>",
				now=True,
			)
			results["email"] = f"✅ Sent to {doc.email}"
		except Exception as e:
			results["email"] = f"❌ Error: {str(e)[:100]}"

	return results

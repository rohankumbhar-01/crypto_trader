import frappe
from frappe import _
from frappe.model.document import Document


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

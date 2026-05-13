import frappe
from frappe import _
from frappe.model.document import Document


class CTExchangeCredential(Document):
	def validate(self):
		self._ensure_single_active_per_user()

	def _ensure_single_active_per_user(self):
		if not self.is_active:
			return
		existing = frappe.db.exists(
			"CT Exchange Credential",
			{"user": self.user, "is_active": 1, "name": ("!=", self.name)},
		)
		if existing:
			frappe.throw(
				_("User {0} already has an active credential. Deactivate it first.").format(self.user)
			)

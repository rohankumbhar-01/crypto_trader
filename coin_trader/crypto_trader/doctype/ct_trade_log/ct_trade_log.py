import frappe
from frappe.model.document import Document


class CTTradeLog(Document):
	pass


def has_permission(doc, ptype, user=None):
	user = user or frappe.session.user
	if "System Manager" in frappe.get_roles(user) or user == "Administrator":
		return True
	return doc.user == user

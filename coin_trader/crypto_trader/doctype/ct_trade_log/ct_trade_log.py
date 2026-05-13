import frappe
from frappe.model.document import Document


class CTTradeLog(Document):
	pass


def has_permission(doc, ptype, user=None):
	user = user or frappe.session.user
	if frappe.has_role("System Manager", user):
		return True
	return doc.user == user

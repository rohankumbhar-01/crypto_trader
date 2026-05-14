import frappe
from frappe.model.document import Document


class CTOpenPosition(Document):
	pass


def has_permission(doc, ptype, user=None):
	user = user or frappe.session.user
	user_roles = frappe.get_roles(user)
	if "System Manager" in user_roles or "Administrator" == user:
		return True
	return doc.user == user

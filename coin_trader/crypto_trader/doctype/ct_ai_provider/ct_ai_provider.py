import frappe
from frappe import _
from frappe.model.document import Document


class CTAIProvider(Document):
	def validate(self):
		self._validate_budget()

	def _validate_budget(self):
		if self.cost_this_month and self.monthly_budget:
			if self.cost_this_month > self.monthly_budget:
				frappe.msgprint(
					_("Cost this month ({0} USD) has exceeded monthly budget ({1} USD).").format(
						self.cost_this_month, self.monthly_budget
					),
					indicator="red",
					title=_("Budget Exceeded"),
				)

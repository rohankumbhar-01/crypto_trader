import frappe
from frappe import _
from frappe.model.document import Document


class CTTradingConfig(Document):
	def validate(self):
		self._validate_dry_run_guard()
		self._validate_risk_params()

	def _validate_dry_run_guard(self):
		if not self.dry_run and self.is_active:
			frappe.msgprint(
				_("Live trading mode is enabled. Ensure paper trading was successful before proceeding."),
				indicator="orange",
				title=_("Live Trading Warning"),
			)

	def _validate_risk_params(self):
		if self.stop_loss_pct <= 0:
			frappe.throw(_("Stop Loss must be greater than 0%."))
		if self.profit_target_pct <= self.stop_loss_pct:
			frappe.throw(_("Profit Target must be greater than Stop Loss."))
		if self.max_daily_loss_inr <= 0:
			frappe.throw(_("Max Daily Loss must be greater than 0."))
		if self.inr_per_trade <= 0:
			frappe.throw(_("INR Per Trade must be greater than 0."))

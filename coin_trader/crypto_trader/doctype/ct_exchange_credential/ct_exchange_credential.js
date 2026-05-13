frappe.ui.form.on("CT Exchange Credential", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Verify Credential"), () => {
				frappe.call({
					method: "coin_trader.exchange.verify_exchange_credential",
					args: { user: frm.doc.user },
					freeze: true,
					freeze_message: __("Verifying with CoinDCX..."),
					callback(r) {
						if (r.message && r.message.valid) {
							frappe.show_alert({
								message: __(
									"Credential verified. INR Balance: {0}",
									[format_currency(r.message.balance_inr, "INR")]
								),
								indicator: "green",
							});
							frm.reload_doc();
						} else {
							frappe.show_alert({
								message: __("Credential verification failed. Check API key and secret."),
								indicator: "red",
							});
						}
					},
				});
			}, __("Actions"));
		}
	},
});

frappe.ui.form.on("CT Notification Config", {
	refresh(frm) {
		frm.add_custom_button(__("🔔 Send Test Notification"), function () {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Please save the document before testing."));
				return;
			}
			frappe.show_progress(__("Sending test notification…"), 60, 100);
			frappe.call({
				method: "coin_trader.crypto_trader.doctype.ct_notification_config.ct_notification_config.send_test_notification",
				args: { doc_name: frm.doc.name },
				callback(r) {
					frappe.hide_progress();
					const res = r.message || {};
					let msg = "<b>Test Results:</b><br><br>";
					if (res.telegram) msg += `<b>Telegram:</b> ${res.telegram}<br>`;
					if (res.email)    msg += `<b>Email:</b> ${res.email}<br>`;
					if (!res.telegram && !res.email) msg += "No channels configured.";
					frappe.msgprint({ title: __("Notification Test"), message: msg, indicator: "blue" });
				},
				error() {
					frappe.hide_progress();
					frappe.msgprint({ title: __("Error"), message: __("Failed to send test notification."), indicator: "red" });
				}
			});
		}, __("Actions"));
	}
});

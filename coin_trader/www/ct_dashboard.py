"""
Context loader for /ct-dashboard

All dashboard data now flows through coin_trader.dashboard_api.get_snapshot()
which the client polls every 5 seconds. This loader only handles auth.
"""

import frappe


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/ct-dashboard"
        raise frappe.Redirect

    context.no_cache = 1
    context.title    = "Coin Trader · Live Dashboard"

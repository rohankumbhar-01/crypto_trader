app_name = "coin_trader"
app_title = "Coin Trader"
app_publisher = "Dexciss Technology"
app_description = "AI + ML Powered CoinDCX Trading Platform"
app_icon = "fa fa-bitcoin"
app_color = "#f39c12"
app_email = "rkrohankumbhar@gmail.com"
app_license = "MIT"
app_version = "0.0.1"

add_to_apps_screen = [
	{
		"name": app_name,
		"logo": "/assets/coin_trader/images/coin_trader_logo.svg",
		"title": app_title,
		"route": "/ct-dashboard",
	}
]

# Scheduler entries
scheduler_events = {
	"cron": {
		"0 2 * * *": [
			"coin_trader.tasks.daily_ml_retrain",
		],
		"*/15 * * * *": [
			"coin_trader.tasks.run_scanner",
		],
	},
	"all": [
		"coin_trader.tasks.monitor_positions",
	],
	"hourly": [],
	"daily": [
		"coin_trader.tasks.generate_daily_summary",
	],
}

# Public assets bundled with the app
app_include_css = []
app_include_js  = []

web_include_css = ["/assets/coin_trader/css/ct_dashboard.css"]
web_include_js  = ["/assets/coin_trader/js/ct_dashboard.js"]

# DocType class overrides
override_doctype_class = {}

# Fixtures — export these with bench export-fixtures
fixtures = [
	{
		"dt": "Module Def",
		"filters": [["app_name", "=", "coin_trader"]],
	},
	{
		"dt": "Workspace",
		"filters": [["name", "=", "Coin Trader"]],
	},
	{
		"dt": "Desktop Icon",
		"filters": [["name", "=", "Coin Trader"]],
	},
	{
		"dt": "Workspace Sidebar",
		"filters": [["name", "=", "Coin Trader"]],
	},
]

# Permissions
has_permission = {
	"CT Open Position": "coin_trader.crypto_trader.doctype.ct_open_position.ct_open_position.has_permission",
	"CT Trade Log": "coin_trader.crypto_trader.doctype.ct_trade_log.ct_trade_log.has_permission",
}

# Boot session
boot_session = "coin_trader.startup.boot_session"

# On login
on_login = []

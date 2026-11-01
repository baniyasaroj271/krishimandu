# KrishiDirect

Nepal-focused agricultural marketplace foundation with secure Flask authentication,
role-based dashboards, profiles, and password recovery.

## Local setup

1. Create a virtual environment and install dependencies:
   `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`
2. Copy `.env.example` to `.env`, set a long random `SECRET_KEY`, and configure
   `DATABASE_URL` for MySQL. SQLite is used when `DATABASE_URL` is omitted.
3. Initialize the schema and optional admin account:
   `.venv\Scripts\flask --app app init-db`
4. Start the server with `.venv\Scripts\flask --app app run`.

For local HTTPS with a temporary development certificate, run:
`python run.py`
Then open `https://127.0.0.1:5001`. Browsers will show a certificate warning
because the development certificate is self-signed. For production, terminate
HTTPS with a trusted certificate in a reverse proxy and set `SESSION_COOKIE_SECURE=1`.

All POST forms use CSRF protection. Passwords are hashed with Werkzeug scrypt,
sessions use HTTP-only SameSite cookies, login attempts are rate limited and
temporarily locked after repeated failures, and account activity is written to
the `audit_log` table. Configure SMTP/SMS provider adapters and a shared
`RATELIMIT_STORAGE_URI` (Redis is recommended) before production deployment;
Reset tokens are single-use and expire. Registration uses a simple email,
phone, password and location form; farm or business details can be completed
later from the profile area.

## Marketplace and Kalimati data

Farmers can create, edit, deactivate and delete detailed product listings,
manage inventory, and accept or update incoming orders. Resellers can browse
approved available listings, compare prices, view product details, find farmers,
and place bulk orders. The Kalimati tracker stores imported publication
snapshots in `market_price`; no current prices are generated when official data
is missing. The official site currently has no documented public API, so admins
can import a permitted CSV export at `/admin/market-prices/import` with the
columns `product_name,minimum_price,maximum_price,average_price,publication_date`.
The supplied Kalimati snapshot can be loaded once with
`.venv\Scripts\flask --app app seed-market-prices`; repeat runs skip existing
product/date rows.

Automatic synchronization uses the official server-rendered daily table at
`https://kalimatimarket.gov.np/price`, preserves the published Bikram Sambat
date, and stores each publication as a historical snapshot. It runs in an
APScheduler background job (default every 60 minutes), can be configured with
`KALIMATI_SYNC_ENABLED`, `KALIMATI_SYNC_INTERVAL_MINUTES`, and
`KALIMATI_SYNC_TIMEOUT_SECONDS`, and can be triggered by an admin at
`/admin/market-prices/import`. The public historical page exposes an official
`/api/price-history` endpoint; the application keeps its own imported snapshots
for reliable charts and does not replace existing data when fetching fails.
rishiDirect – Agri-Marketplace & Kalimati Mandi Price Tracker
Project concept
 Nepal-focused


KrishiDirect is a digital agricultural marketplace that connects local farmers directly with vegetable and fruit resellers, wholesalers, retailers, hotels, restaurants, and other bulk buyers.

The main goal is to eliminate unnecessary middlemen, help farmers receive fair prices, allow resellers to purchase fresh products at competitive rates, and provide transparent daily agricultural market prices from Kalimati Vegetable and Fruit Market, Nepal.

Core concept:

Farmer → KrishiDirect → Reseller / Wholesaler / Retailer

The platform should have two major systems:
1. Agricultural Marketplace: Farmers list their products, buyers compare prices, negotiate, place bulk orders, and arrange delivery.
2. Mandi Price Intelligence: Users check daily Kalimati prices, historical prices, price fluctuations, and market trends before buying or selling.
1. Main user roles
customer

Finds local products, compares farmer prices with Kalimati prices, negotiates, and purchases prices.

Admin

Manages market data, handles complaints, monitors transactions, and controls platform operations.

Lists vegetables and fruits, manages stock, receives orders, sets prices, and tracks earnings.

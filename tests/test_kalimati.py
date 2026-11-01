import unittest
from unittest.mock import patch

from app import create_app
from extensions import db
from kalimati import fetch_price_history, parse_daily_prices, sync_daily_prices
from models import MarketPrice, MarketSync


HTML = """
<h4>संकलित दैनिक थोक मूल्य - वि.सं. कार्तिक १५, २०८३</h4>
<table><tr><th>कृषि उपज</th><th>ईकाइ</th><th>न्यूनतम</th><th>अधिकतम</th><th>औसत</th></tr>
<tr><td>आलु सेतो</td><td>के.जी.</td><td>रू २८.००</td><td>रू ३२.००</td><td>रू ३०.००</td></tr>
</table>
"""


class KalimatiSyncTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
        })
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        self.context.pop()

    def test_parser_preserves_nepali_name_and_bs_date(self):
        rows = parse_daily_prices(HTML)
        self.assertEqual(rows[0]["product_name"], "आलु सेतो")
        self.assertEqual(rows[0]["unit"], "kg")
        self.assertEqual(rows[0]["average_price"], 30)
        self.assertEqual(rows[0]["publication_date_bs"], "कार्तिक १५, २०८३")

    @patch("kalimati.requests.get")
    def test_sync_is_idempotent(self, get):
        get.return_value.raise_for_status.return_value = None
        get.return_value.text = HTML
        first = sync_daily_prices(self.app)
        second = sync_daily_prices(self.app)
        self.assertEqual(first["imported"], 1)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(MarketPrice.query.count(), 1)
        self.assertEqual(MarketSync.query.filter_by(status="success").count(), 2)

    @patch("kalimati.requests.Session")
    def test_history_adapter_uses_official_payload(self, session_factory):
        session = session_factory.return_value
        session.get.return_value.raise_for_status.return_value = None
        session.get.return_value.text = '<input id="csrf" value="token">'
        session.post.return_value.raise_for_status.return_value = None
        session.post.return_value.json.return_value = {
            "commodity": "आलु सेतो",
            "prices": {"date": ["2026-10-01"], "avg": [30]},
        }
        payload = fetch_price_history("101.1", "2026-10-01", "2026-11-01")
        self.assertEqual(payload["prices"]["avg"], [30])
        session.post.assert_called_once()
        self.assertTrue(session.post.call_args.args[0].endswith("/101.1"))
        self.assertEqual(session.post.call_args.kwargs["data"]["_token"], "token")

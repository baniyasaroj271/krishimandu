import logging
import re
import threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests
from bs4 import BeautifulSoup
from flask import current_app

from extensions import db
from models import MarketPrice, MarketSync

LOGGER = logging.getLogger(__name__)
DAILY_URL = "https://kalimatimarket.gov.np/price"
HISTORY_URL = "https://kalimatimarket.gov.np/api/price-history"
HISTORY_PAGE_URL = "https://kalimatimarket.gov.np/price-history"
_sync_lock = threading.Lock()
_NEPALI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


class KalimatiSyncError(RuntimeError):
    pass


def _western_digits(value):
    return value.translate(_NEPALI_DIGITS)


def _decimal(value):
    cleaned = _western_digits(value).replace(",", "")
    cleaned = re.sub(r"[^\d.]", "", cleaned)
    if not cleaned:
        raise InvalidOperation
    return Decimal(cleaned)


def _unit(value):
    normalized = " ".join(_western_digits(value).lower().split())
    if "दर्जन" in normalized:
        return "dozen"
    if "गोटा" in normalized:
        return "piece"
    if "के" in normalized and "जी" in normalized:
        return "kg"
    return normalized or "unknown"


def _category(name):
    fruit_markers = ("स्याउ", "केरा", "कागती", "अनार", "सुन्तला", "तरबुजा", "मौसम",
                     "जुनार", "कटहर", "नासपाती", "मेवा", "अम्बा", "लप्सी", "किवि",
                     "शरीफा", "आभोकाडो", "अमला", "नरिवल", "ड्रागन")
    spice_markers = ("अदुवा", "खुर्सानी", "लसुन", "धनिया", "छ्यापी")
    if any(marker in name for marker in fruit_markers):
        return "Fruit"
    if any(marker in name for marker in spice_markers):
        return "Spice"
    return "Vegetable"


def _publication_date_bs(soup):
    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = heading.get_text(" ", strip=True)
        match = re.search(r"वि\.सं\.\s*(?P<date>[^\n]+)", text)
        if match and re.search(r"[०-९0-9]", match.group("date")):
            return match.group("date").strip(" -")
    return None


def parse_daily_prices(html, source_url=DAILY_URL):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise KalimatiSyncError("The official price table was not found.")
    rows = []
    publication_bs = _publication_date_bs(soup)
    if not publication_bs:
        raise KalimatiSyncError("The official publication date was not found.")
    for row in table.find_all("tr")[1:]:
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        if len(cells) < 5:
            continue
        product_name, unit, minimum, maximum, average = cells[:5]
        if not product_name:
            continue
        try:
            minimum_value = _decimal(minimum)
            maximum_value = _decimal(maximum)
            average_value = _decimal(average)
        except InvalidOperation:
            LOGGER.warning("Skipping malformed Kalimati row: %s", cells)
            continue
        if minimum_value < 0 or maximum_value < minimum_value or average_value < 0:
            LOGGER.warning("Skipping invalid Kalimati row: %s", cells)
            continue
        rows.append({
            "product_name": product_name,
            "unit": _unit(unit),
            "min_price": minimum_value,
            "max_price": maximum_value,
            "average_price": average_value,
            "category": _category(product_name),
            "publication_date_bs": publication_bs,
            "source_url": source_url,
        })
    if not rows:
        raise KalimatiSyncError("The official price table contained no valid rows.")
    return rows


def fetch_price_history(commodity_id, start_date, end_date, locale="np", session=None):
    """Fetch one official historical series using the site's documented form."""
    client = session or requests.Session()
    headers = {"User-Agent": "KrishiMandu/1.0 (+market-price-sync)"}
    token_response = client.get(
        HISTORY_PAGE_URL,
        headers=headers,
        timeout=current_app.config["KALIMATI_SYNC_TIMEOUT_SECONDS"],
    )
    token_response.raise_for_status()
    token = BeautifulSoup(token_response.text, "html.parser").select_one("#csrf")
    if token is None or not token.get("value"):
        raise KalimatiSyncError("The official historical form token was not found.")
    response = client.post(
        f"{HISTORY_URL}/{commodity_id}",
        data={"locale": locale, "from": start_date, "to": end_date, "_token": token["value"]},
        headers=headers,
        timeout=current_app.config["KALIMATI_SYNC_TIMEOUT_SECONDS"],
    )
    response.raise_for_status()
    payload = response.json()
    prices = payload.get("prices") if isinstance(payload, dict) else None
    if not isinstance(prices, dict) or not isinstance(prices.get("date"), list):
        raise KalimatiSyncError("The official historical response had an unexpected format.")
    return payload


def sync_daily_prices(app=None):
    app = app or current_app._get_current_object()
    if not _sync_lock.acquire(blocking=False):
        return {"status": "already_running", "imported": 0}
    started = datetime.now(timezone.utc)
    sync = MarketSync(started_at=started, status="running", source_url=DAILY_URL)
    try:
        with app.app_context():
            db.session.add(sync)
            db.session.commit()
            response = requests.get(
                DAILY_URL,
                headers={"User-Agent": "KrishiMandu/1.0 (+market-price-sync)"},
                timeout=app.config["KALIMATI_SYNC_TIMEOUT_SECONDS"],
            )
            response.raise_for_status()
            rows = parse_daily_prices(response.text)
            fetched_at = datetime.now(timezone.utc)
            imported = 0
            for row in rows:
                identity = f"{row['product_name']}|{row['unit']}"
                existing = MarketPrice.query.filter_by(
                    source_identity=identity,
                    market_date_bs=row["publication_date_bs"],
                ).first()
                if existing:
                    continue
                db.session.add(MarketPrice(
                    product_name=row["product_name"],
                    product_name_ne=row["product_name"],
                    market_name="Kalimati",
                    unit=row["unit"],
                    min_price=row["min_price"],
                    max_price=row["max_price"],
                    average_price=row["average_price"],
                    category=row["category"],
                    source_date=None,
                    price_date=fetched_at.date(),
                    source_url=row["source_url"],
                    synced_at=fetched_at,
                    fetched_at=fetched_at,
                    sync_status="success",
                    market_date_bs=row["publication_date_bs"],
                    source_identity=identity,
                ))
                imported += 1
            sync.status = "success"
            sync.completed_at = fetched_at
            sync.publication_date_bs = rows[0]["publication_date_bs"]
            sync.imported_count = imported
            db.session.commit()
            return {"status": "success", "imported": imported, "publication_date_bs": sync.publication_date_bs}
    except (requests.RequestException, KalimatiSyncError, InvalidOperation) as exc:
        LOGGER.exception("Kalimati synchronization failed")
        with app.app_context():
            sync.status = "failed"
            sync.completed_at = datetime.now(timezone.utc)
            sync.error_message = str(exc)[:2000]
            db.session.add(sync)
            db.session.commit()
        return {"status": "failed", "imported": 0, "error": str(exc)}
    finally:
        _sync_lock.release()

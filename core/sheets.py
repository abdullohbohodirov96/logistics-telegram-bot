import logging
import os
import json
import asyncio
import gspread
from google.oauth2.service_account import Credentials
from core.config import GOOGLE_SHEET_ID, ORDERS_SHEET_NAME, DRIVERS_SHEET_NAME

logger = logging.getLogger(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

_GSPREAD_CLIENT = None

def get_gspread_client():
    """Authenticates and returns a cached gspread client."""
    global _GSPREAD_CLIENT
    if _GSPREAD_CLIENT:
        return _GSPREAD_CLIENT
        
    try:
        creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
        creds_dict = None
        
        if creds_json:
            creds_dict = json.loads(creds_json)
        elif os.path.exists('credentials.json'):
            with open('credentials.json', 'r') as f:
                creds_dict = json.load(f)
        else:
            logger.error("❌ No Google credentials found!")
            return None

        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        _GSPREAD_CLIENT = gspread.authorize(creds)
        logger.info(f"✅ Google Sheets connected as: {creds_dict.get('client_email')}")
        return _GSPREAD_CLIENT
    except Exception as e:
        logger.error(f"❌ Auth error: {e}")
        return None

def fuzzy_match_header(headers, target_names):
    for i, h in enumerate(headers):
        clean_h = str(h).strip().lower()
        if clean_h in [t.lower() for t in target_names]:
            return i
    return -1

def get_new_orders():
    client = get_gspread_client()
    if not client: return []
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(ORDERS_SHEET_NAME)
        values = worksheet.get_all_values()
        
        if not values or len(values) < 2: return []

        headers = values[0]
        id_idx = fuzzy_match_header(headers, ['id', 'order_id', 'id_order'])
        car_idx = fuzzy_match_header(headers, ['car', 'car_number', 'mashina', 'moshina'])
        addr_idx = fuzzy_match_header(headers, ['address', 'manzil'])
        cargo_idx = fuzzy_match_header(headers, ['cargo', 'yuk'])
        status_idx = fuzzy_match_header(headers, ['status', 'holat'])

        new_orders = []
        for i, row in enumerate(values[1:], start=2):
            status_val = row[status_idx].strip() if len(row) > status_idx else ""
            # Process only empty status rows
            if not status_val:
                order_id = row[id_idx].strip() if len(row) > id_idx else f"row_{i}"
                new_orders.append({
                    'row_index': i,
                    'order_id': order_id,
                    'car_number': row[car_idx].strip().upper() if len(row) > car_idx else "",
                    'address': row[addr_idx] if len(row) > addr_idx else "-",
                    'cargo': row[cargo_idx] if len(row) > cargo_idx else "",
                    'comment': row[fuzzy_match_header(headers, ['comment', 'izoh'])] if len(row) > 5 else "",
                })
        return new_orders
    except Exception as e:
        logger.error(f"Error get_new_orders: {e}")
        return []

def get_drivers():
    client = get_gspread_client()
    if not client: return {}
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
        values = worksheet.get_all_values()
        if not values or len(values) < 2: return {}

        drivers = {}
        for row in values[1:]:
            if len(row) < 3: continue
            car_raw = str(row[0]).strip().upper()
            if not car_raw: continue
            drivers[car_raw] = {'driver_name': row[1], 'telegram_id': row[2], 'status': row[3] if len(row) > 3 else "IDLE"}
        return drivers
    except Exception as e:
        logger.error(f"Error get_drivers: {e}")
        return {}

def update_order_status(row_index, status):
    client = get_gspread_client()
    if not client: return
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(ORDERS_SHEET_NAME)
        headers = worksheet.row_values(1)
        status_idx = fuzzy_match_header(headers, ['status', 'holat'])
        if status_idx != -1:
            worksheet.update_cell(row_index, status_idx + 1, status)
            logger.info(f"✅ Sheets Row {row_index} -> {status}")
    except Exception as e:
        logger.error(f"Error update_order_status: {e}")

def update_order_status_by_order_id(order_id, status):
    client = get_gspread_client()
    if not client: return
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(ORDERS_SHEET_NAME)
        cell = worksheet.find(str(order_id).strip(), in_column=1) # Assume ID is col 1
        if cell:
            headers = worksheet.row_values(1)
            status_idx = fuzzy_match_header(headers, ['status', 'holat'])
            if status_idx != -1:
                worksheet.update_cell(cell.row, status_idx + 1, status)
    except Exception as e:
        logger.error(f"Error update_order_status_by_order_id: {e}")

def update_driver_status_sheet(car_number, status, order_id=""):
    client = get_gspread_client()
    if not client: return
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
        cell = worksheet.find(str(car_number).strip().upper(), in_column=1)
        if cell:
            worksheet.update_cell(cell.row, 4, status)
            worksheet.update_cell(cell.row, 5, order_id)
    except Exception as e:
        logger.error(f"Error update_driver_status_sheet: {e}")

def get_driver_by_tid(tid):
    client = get_gspread_client()
    if not client: return None
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
        values = worksheet.get_all_values()
        for row in values[1:]:
            if len(row) > 2 and str(row[2]) == str(tid):
                return {'car_number': row[0], 'driver_name': row[1], 'status': row[3] if len(row) > 3 else "", 'current_order_id': row[4] if len(row) > 4 else ""}
        return None
    except Exception as e:
        logger.error(f"Error get_driver_by_tid: {e}")
        return None

def get_drivers_status_list():
    client = get_gspread_client()
    if not client: return []
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
        values = worksheet.get_all_values()
        if not values or len(values) < 2: return []
        return [{'car_number': r[0].strip().upper(), 'driver_name': r[1], 'status': r[3] if len(r) > 3 else "IDLE", 'order_id': r[4] if len(r) > 4 else ""} for r in values[1:] if r]
    except Exception as e:
        logger.error(f"Error get_drivers_status_list: {e}")
        return []

def get_all_drivers_list():
    client = get_gspread_client()
    if not client: return []
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
        values = worksheet.get_all_values()
        return [(r[1], r[2]) for r in values[1:] if len(r) >= 3 and r[2]]
    except Exception as e:
        logger.error(f"Error get_all_drivers_list: {e}")
        return []

def get_all_cars_list():
    client = get_gspread_client()
    if not client: return []
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
        values = worksheet.get_all_values()
        return sorted(list(set([r[0].strip().upper() for r in values[1:] if r])))
    except Exception as e:
        logger.error(f"Error get_all_cars_list: {e}")
        return []

import logging
import os
import json
import asyncio
import gspread
from google.oauth2.service_account import Credentials
from core.config import GOOGLE_SHEET_ID, ORDERS_SHEET_NAME, DRIVERS_SHEET_NAME

logger = logging.getLogger(__name__)

# Scopes required for Google Sheets and Drive
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

def get_gspread_client():
    """Authenticates and returns a gspread client using Service Account."""
    try:
        creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
        creds_dict = None
        
        if creds_json:
            logger.info("Using credentials from GOOGLE_CREDENTIALS_JSON environment variable.")
            creds_dict = json.loads(creds_json)
        elif os.path.exists('credentials.json'):
            logger.info("Using credentials from local credentials.json file.")
            with open('credentials.json', 'r') as f:
                creds_dict = json.load(f)
        else:
            logger.error("❌ No Google credentials found! Set GOOGLE_CREDENTIALS_JSON or provide credentials.json")
            return None

        if not creds_dict:
            logger.error("❌ Credentials dictionary is empty.")
            return None

        # Log credential info for debugging
        logger.info(f"Credential Type: {creds_dict.get('type')}")
        logger.info(f"Client Email: {creds_dict.get('client_email', 'NOT FOUND')}")

        if creds_dict.get('type') != 'service_account':
            logger.error("❌ ERROR: Provided credentials are NOT for a Service Account. "
                         "Please use a Service Account JSON key.")
            return None

        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        logger.error(f"❌ Error authenticating Google Service Account: {e}")
        return None

def fuzzy_match_header(headers, target_names):
    """Find column index by multiple possible header names (case insensitive)."""
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
        comment_idx = fuzzy_match_header(headers, ['comment', 'izoh'])
        status_idx = fuzzy_match_header(headers, ['status', 'holat'])

        new_orders = []
        for i, row in enumerate(values[1:], start=2):
            # Check if status column is empty
            status_val = row[status_idx].strip() if len(row) > status_idx else ""
            if not status_val:
                order_id = row[id_idx].strip() if len(row) > id_idx else f"row_{i}"
                if not order_id: continue
                
                new_orders.append({
                    'row_index': i,
                    'order_id': order_id,
                    'car_number': row[car_idx].strip().upper() if len(row) > car_idx else "",
                    'address': row[addr_idx] if len(row) > addr_idx else "-",
                    'cargo': row[cargo_idx] if len(row) > cargo_idx else "-",
                    'comment': row[comment_idx] if len(row) > comment_idx else "",
                })
        return new_orders
    except Exception as e:
        logger.error(f"Error fetching orders from Sheets: {e}")
        return []

def get_drivers():
    """Returns a dict of car_number -> driver_info from Drivers sheet."""
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
            
            drivers[car_raw] = {
                'driver_name': row[1] if len(row) > 1 else "Noma'lum",
                'telegram_id': row[2] if len(row) > 2 else None,
                'status': row[3] if len(row) > 3 else "IDLE"
            }
        
        logger.info(f"Loaded drivers from sheet: {list(drivers.keys())}")
        return drivers
    except Exception as e:
        logger.error(f"Error fetching drivers from Sheets: {e}")
        return {}

def update_order_status(row_index, status):
    client = get_gspread_client()
    if not client: return
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(ORDERS_SHEET_NAME)
        headers = worksheet.row_values(1)
        status_idx = fuzzy_match_header(headers, ['status', 'holat'])
        if status_idx == -1: return

        worksheet.update_cell(row_index, status_idx + 1, status)
        logger.info(f"Sheets: Row {row_index} status updated to {status}")
    except Exception as e:
        logger.error(f"Error updating Sheets status: {e}")

def find_order_row(order_id: str) -> int:
    client = get_gspread_client()
    if not client: return -1
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(ORDERS_SHEET_NAME)
        # Using find to search for the order_id in the entire worksheet
        cell = worksheet.find(str(order_id).strip())
        if cell: return cell.row
        return -1
    except Exception as e:
        logger.error(f"Error finding order row: {e}")
        return -1

def update_order_status_by_order_id(order_id: str, status: str):
    row_index = find_order_row(order_id)
    if row_index != -1:
        update_order_status(row_index, status)
    else:
        logger.error(f"Could not update Sheets status for {order_id}: Row not found.")

def update_driver_status_sheet(car_number, status, order_id=""):
    """Update status and current_order_id in Drivers sheet."""
    client = get_gspread_client()
    if not client: return
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
        target_car = str(car_number).strip().upper()
        
        cell = worksheet.find(target_car, in_column=1)
        if cell:
            # Column 4 is status, Column 5 is current_order_id
            worksheet.update_cell(cell.row, 4, status)
            worksheet.update_cell(cell.row, 5, order_id)
            logger.info(f"Driver {target_car} status updated to {status}")
    except Exception as e:
        logger.error(f"Error updating driver status: {e}")

def get_driver_by_tid(tid):
    client = get_gspread_client()
    if not client: return None
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
        values = worksheet.get_all_values()
        
        for row in values[1:]:
            if len(row) > 2 and str(row[2]) == str(tid):
                return {
                    'car_number': row[0],
                    'driver_name': row[1],
                    'status': row[3] if len(row) > 3 else "",
                    'current_order_id': row[4] if len(row) > 4 else ""
                }
        return None
    except Exception as e:
        logger.error(f"Error getting driver by tid: {e}")
        return None

def get_drivers_status_list():
    client = get_gspread_client()
    if not client: return []
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
        values = worksheet.get_all_values()
        
        if not values or len(values) < 2: return []
        
        data = []
        for row in values[1:]:
            if not row: continue
            data.append({
                'car_number': row[0].strip().upper() if len(row) > 0 else "-",
                'driver_name': row[1] if len(row) > 1 else "-",
                'status': row[3] if len(row) > 3 else "IDLE",
                'order_id': row[4] if len(row) > 4 else ""
            })
        return data
    except Exception as e:
        logger.error(f"Error getting drivers status list: {e}")
        return []

def get_all_drivers_list():
    client = get_gspread_client()
    if not client: return []
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
        values = worksheet.get_all_values()
        
        drivers = []
        for row in values[1:]:
            if len(row) >= 3 and row[2]:
                drivers.append((row[1], row[2]))
        return drivers
    except Exception as e:
        logger.error(f"Error getting all drivers list: {e}")
        return []

def get_all_cars_list():
    client = get_gspread_client()
    if not client: return []
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
        values = worksheet.get_all_values()
        
        cars = sorted(list(set([row[0].strip().upper() for row in values[1:] if row])))
        return cars
    except Exception as e:
        logger.error(f"Error getting all cars list: {e}")
        return []

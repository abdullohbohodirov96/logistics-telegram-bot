import logging
import re
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build
from core.config import GOOGLE_SERVICE_ACCOUNT_INFO, GOOGLE_SHEET_ID, DRIVERS_SHEET_NAME, ORDERS_SHEET_NAME
from core.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Cache the Sheets service object
_sheets_service = None

def get_sheets_service():
    global _sheets_service
    if _sheets_service is not None:
        return _sheets_service
    if not GOOGLE_SERVICE_ACCOUNT_INFO or not GOOGLE_SHEET_ID:
        logger.error("Google Sheets credentials not configured.")
        return None
    try:
        creds = service_account.Credentials.from_service_account_info(
            GOOGLE_SERVICE_ACCOUNT_INFO, scopes=SCOPES)
        service = build('sheets', 'v4', credentials=creds)
        _sheets_service = service.spreadsheets()
        return _sheets_service
    except Exception as e:
        logger.error(f"Error initializing Sheets service: {e}")
        return None

def normalize_car(val: str):
    if not val: return ""
    # Uppercase and remove all spaces/non-alphanumeric
    return re.sub(r'[^A-Z0-9]', '', val.strip().upper())

def clean_tid(val: str):
    if not val: return None
    # Remove invisible chars, apostrophes, spaces
    clean = re.sub(r'[^0-9]', '', val.strip())
    if clean.isdigit():
        return int(clean)
    return None

def get_drivers():
    """Read driver list from DRIVERS sheet using header names. Cached 30s."""
    cached = cache_get('drivers_dict', 30)
    if cached is not None:
        return cached
        
    sheets = get_sheets_service()
    if not sheets: return {}
    
    try:
        result = sheets.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f'{DRIVERS_SHEET_NAME}!A1:Z'
        ).execute()
        values = result.get('values', [])
        if not values: return {}
        
        headers = [h.strip().lower() for h in values[0]]
        rows = values[1:]
        
        # Identify column indices
        try:
            idx_car = headers.index('car_number')
            idx_name = headers.index('driver_name')
            idx_tid = headers.index('driver_user_id')
        except ValueError as e:
            logger.error(f"DRIVERS header missing: {e}")
            return {}
            
        drivers = {}
        for i, row in enumerate(rows):
            if len(row) <= max(idx_car, idx_name, idx_tid): continue
            norm_car = normalize_car(row[idx_car])
            parsed_tid = clean_tid(row[idx_tid])
            if norm_car and parsed_tid:
                drivers[norm_car] = {
                    'driver_name': row[idx_name].strip(),
                    'telegram_id': parsed_tid
                }
        
        cache_set('drivers_dict', drivers)
        return drivers
    except Exception as e:
        logger.error(f"Error getting drivers: {e}")
        return {}

def get_new_orders():
    """Read orders from ORDERS sheet using headers."""
    sheets = get_sheets_service()
    if not sheets: return []
    
    try:
        result = sheets.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f'{ORDERS_SHEET_NAME}!A1:Z'
        ).execute()
        values = result.get('values', [])
        if not values: return []
        
        headers = [h.strip().lower() for h in values[0]]
        rows = values[1:]
        
        try:
            idx_id = headers.index('order_id')
            idx_car = headers.index('car_number')
            idx_addr = headers.index('address')
            idx_cargo = headers.index('cargo')
            idx_comment = headers.index('comment')
            idx_status = headers.index('status')
        except ValueError as e:
            logger.error(f"ORDERS header missing: {e}")
            return []
            
        new_orders = []
        for i, row in enumerate(rows):
            if len(row) <= idx_status: continue
            if row[idx_status].strip().upper() == 'SEND':
                order = {
                    'row_index': i + 2,
                    'order_id': row[idx_id].strip(),
                    'car_number': normalize_car(row[idx_car]),
                    'address': row[idx_addr].strip() if len(row) > idx_addr else '',
                    'cargo': row[idx_cargo].strip() if len(row) > idx_cargo else '',
                    'comment': row[idx_comment].strip() if len(row) > idx_comment else ''
                }
                new_orders.append(order)
        return new_orders
    except Exception as e:
        logger.error(f"Error getting new orders: {e}")
        return []

def update_order_status(row_index: int, status: str):
    """Update status in ORDERS sheet."""
    sheets = get_sheets_service()
    if not sheets: return
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{ORDERS_SHEET_NAME}!1:1').execute()
        headers = [h.strip().lower() for h in result.get('values', [[]])[0]]
        idx = headers.index('status')
        col_letter = chr(ord('A') + idx)
        sheets.values().update(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f'{ORDERS_SHEET_NAME}!{col_letter}{row_index}',
            valueInputOption='USER_ENTERED',
            body={'values': [[status]]}
        ).execute()
    except Exception as e:
        logger.error(f"Error updating order status: {e}")

def update_driver_status_sheet(car_number: str, status: str, current_order_id: str = ""):
    """Update driver status in DRIVERS sheet."""
    sheets = get_sheets_service()
    if not sheets: return
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A1:Z').execute()
        values = result.get('values', [])
        if not values: return
        headers = [h.strip().lower() for h in values[0]]
        idx_car = headers.index('car_number')
        idx_status = headers.index('status')
        idx_order = headers.index('current_order_id')
        norm_target = normalize_car(car_number)
        row_num = -1
        for i, row in enumerate(values[1:]):
            if len(row) > idx_car and normalize_car(row[idx_car]) == norm_target:
                row_num = i + 2
                break
        if row_num != -1:
            col_status = chr(ord('A') + idx_status)
            col_order = chr(ord('A') + idx_order)
            sheets.values().update(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f'{DRIVERS_SHEET_NAME}!{col_status}{row_num}',
                valueInputOption='USER_ENTERED',
                body={'values': [[status]]}
            ).execute()
            sheets.values().update(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f'{DRIVERS_SHEET_NAME}!{col_order}{row_num}',
                valueInputOption='USER_ENTERED',
                body={'values': [[current_order_id]]}
            ).execute()
    except Exception as e:
        logger.error(f"Error updating DRIVERS status: {e}")

def get_all_drivers_list():
    cached = cache_get('master_drivers_list', 30)
    if cached is not None: return cached
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A1:Z').execute()
        values = result.get('values', [])
        if not values: return []
        headers = [h.strip().lower() for h in values[0]]
        idx_tid = headers.index('driver_user_id')
        idx_name = headers.index('driver_name')
        drivers = []
        seen = set()
        for row in values[1:]:
            tid = clean_tid(row[idx_tid])
            name = row[idx_name].strip()
            if tid and tid not in seen:
                drivers.append((name, str(tid)))
                seen.add(tid)
        cache_set('master_drivers_list', drivers)
        return drivers
    except Exception: return []

def get_all_cars_list():
    cached = cache_get('master_cars_list', 30)
    if cached is not None: return cached
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A1:Z').execute()
        values = result.get('values', [])
        if not values: return []
        headers = [h.strip().lower() for h in values[0]]
        idx_car = headers.index('car_number')
        cars = set()
        for row in values[1:]:
            norm = normalize_car(row[idx_car])
            if norm: cars.add(norm)
        res = sorted(list(cars))
        cache_set('master_cars_list', res)
        return res
    except Exception: return []

def get_drivers_status():
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A2:Z').execute()
        return result.get('values', [])
    except Exception: return []

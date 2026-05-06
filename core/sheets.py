import logging
import re
import asyncio
from google.oauth2 import service_account
from googleapiclient.discovery import build
from core.config import GOOGLE_SERVICE_ACCOUNT_INFO, GOOGLE_SHEET_ID, DRIVERS_SHEET_NAME, ORDERS_SHEET_NAME
from core.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

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
    return re.sub(r'[^A-Z0-9]', '', str(val).strip().upper())

def clean_tid(val: str):
    if not val: return None
    clean = re.sub(r'[^0-9]', '', str(val).strip())
    if clean.isdigit():
        return int(clean)
    return None

def get_driver_by_tid(tid: int):
    """Find driver by Telegram user_id."""
    sheets = get_sheets_service()
    if not sheets: return None
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A1:Z').execute()
        values = result.get('values', [])
        if not values: return None
        
        headers = [h.strip().lower() for h in values[0]]
        try:
            idx_car = headers.index('car_number')
            idx_name = headers.index('driver_name')
            idx_tid = headers.index('driver_user_id')
            idx_status = headers.index('status')
            idx_order = headers.index('current_order_id')
        except ValueError: return None
        
        for row in values[1:]:
            if len(row) <= idx_tid: continue
            if clean_tid(row[idx_tid]) == tid:
                return {
                    'car_number': row[idx_car].strip() if len(row) > idx_car else '',
                    'driver_name': row[idx_name].strip() if len(row) > idx_name else '',
                    'status': row[idx_status].strip() if len(row) > idx_status else 'BO\'SH',
                    'current_order_id': row[idx_order].strip() if len(row) > idx_order else ''
                }
        return None
    except Exception as e:
        logger.error(f"Error finding driver by TID: {e}")
        return None

def get_new_orders():
    """Read orders from ORDERS sheet where status is empty or 'NEW'."""
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{ORDERS_SHEET_NAME}!A1:Z').execute()
        values = result.get('values', [])
        if not values: return []
        
        headers = [h.strip().lower() for h in values[0]]
        try:
            idx_id = headers.index('order_id')
            idx_car = headers.index('car_number')
            idx_addr = headers.index('address')
            idx_cargo = headers.index('cargo')
            idx_comment = headers.index('comment')
            idx_status = headers.index('status')
        except ValueError: return []
        
        new_orders = []
        for i, row in enumerate(values[1:]):
            status = ""
            if len(row) > idx_status:
                status = row[idx_status].strip().upper()
            
            if status in ['', 'NEW', 'YANGI']:
                new_orders.append({
                    'row_index': i + 2,
                    'order_id': row[idx_id].strip() if len(row) > idx_id else str(i+1),
                    'car_number': normalize_car(row[idx_car]) if len(row) > idx_car else '',
                    'address': row[idx_addr].strip() if len(row) > idx_addr else '',
                    'cargo': row[idx_cargo].strip() if len(row) > idx_cargo else '',
                    'comment': row[idx_comment].strip() if len(row) > idx_comment else ''
                })
        return new_orders
    except Exception as e:
        if "Requested entity was not found" in str(e):
            logger.error(f"❌ XATO: '{ORDERS_SHEET_NAME}' tab topilmadi.")
            try:
                # Debug: list all available sheets
                meta = sheets.get(spreadsheetId=GOOGLE_SHEET_ID).execute()
                titles = [s['properties']['title'] for s in meta.get('sheets', [])]
                logger.info(f"Mavjud sheet nomlari: {titles}")
            except: pass
        else:
            logger.error(f"Error getting new orders: {e}")
        return []

def update_order_status(row_index: int, status: str):
    """ONLY update the status column in ORDERS sheet. No extra columns added."""
    sheets = get_sheets_service()
    if not sheets: return
    try:
        # Find 'status' column index
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{ORDERS_SHEET_NAME}!1:1').execute()
        headers = [h.strip().lower() for h in result.get('values', [[]])[0]]
        if 'status' not in headers:
            logger.error("'status' column not found in ORDERS sheet.")
            return
            
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
    """Update driver status and current_order_id in DRIVERS sheet."""
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
            sheets.values().batchUpdate(
                spreadsheetId=GOOGLE_SHEET_ID,
                body={
                    'valueInputOption': 'USER_ENTERED',
                    'data': [
                        {'range': f'{DRIVERS_SHEET_NAME}!{col_status}{row_num}', 'values': [[status]]},
                        {'range': f'{DRIVERS_SHEET_NAME}!{col_order}{row_num}', 'values': [[current_order_id]]}
                    ]
                }
            ).execute()
    except Exception as e:
        logger.error(f"Error updating driver status: {e}")

def get_drivers_status_list():
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A1:Z').execute()
        values = result.get('values', [])
        if not values: return []
        
        headers = [h.strip().lower() for h in values[0]]
        idx_car = headers.index('car_number')
        idx_name = headers.index('driver_name')
        idx_status = headers.index('status')
        idx_order = headers.index('current_order_id')
        
        drivers = []
        for row in values[1:]:
            if len(row) <= idx_status: continue
            drivers.append({
                'car_number': row[idx_car].strip() if len(row) > idx_car else '?',
                'driver_name': row[idx_name].strip() if len(row) > idx_name else '?',
                'status': row[idx_status].strip() if len(row) > idx_status else 'BO\'SH',
                'order_id': row[idx_order].strip() if len(row) > idx_order else ''
            })
        return drivers
    except Exception: return []

def get_all_drivers_list():
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A1:Z').execute()
        values = result.get('values', [])
        if not values: return []
        headers = [h.strip().lower() for h in values[0]]
        idx_name = headers.index('driver_name')
        idx_tid = headers.index('driver_user_id')
        drivers = []
        seen_tids = set()
        for row in values[1:]:
            if len(row) <= max(idx_name, idx_tid): continue
            name = row[idx_name].strip()
            tid = clean_tid(row[idx_tid])
            if name and tid and tid not in seen_tids:
                drivers.append((name, str(tid)))
                seen_tids.add(tid)
        return drivers
    except: return []

def get_all_cars_list():
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
            if len(row) <= idx_car: continue
            car = normalize_car(row[idx_car])
            if car: cars.add(car)
        return sorted(list(cars))
    except: return []

def get_drivers():
    sheets = get_sheets_service()
    if not sheets: return {}
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A1:Z').execute()
        values = result.get('values', [])
        if not values: return {}
        headers = [h.strip().lower() for h in values[0]]
        idx_car = headers.index('car_number')
        idx_name = headers.index('driver_name')
        idx_tid = headers.index('driver_user_id')
        drivers = {}
        for row in values[1:]:
            if len(row) <= max(idx_car, idx_name, idx_tid): continue
            norm_car = normalize_car(row[idx_car])
            tid = clean_tid(row[idx_tid])
            if norm_car and tid:
                drivers[norm_car] = {'driver_name': row[idx_name].strip(), 'telegram_id': tid}
        return drivers
    except: return {}

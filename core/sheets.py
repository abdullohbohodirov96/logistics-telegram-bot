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
    val = str(val).upper().strip()
    replacements = {'А':'A','В':'B','Е':'E','К':'K','М':'M','Н':'H','О':'O','Р':'P','С':'C','Т':'T','Х':'X'}
    for cyr, lat in replacements.items(): val = val.replace(cyr, lat)
    return re.sub(r'[^A-Z0-9]', '', val)

def clean_tid(val: str):
    if not val: return None
    clean = re.sub(r'[^0-9]', '', str(val).strip())
    return int(clean) if clean.isdigit() else None

def fuzzy_match_header(headers, target_names):
    """Find column index by trying multiple possible names."""
    norm_headers = [re.sub(r'[^a-z0-9]', '', h.lower()) for h in headers]
    for target in target_names:
        norm_target = re.sub(r'[^a-z0-9]', '', target.lower())
        if norm_target in norm_headers:
            return norm_headers.index(norm_target)
    return -1

def get_drivers():
    sheets = get_sheets_service()
    if not sheets: return {}
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A1:Z100').execute()
        values = result.get('values', [])
        if not values: return {}
        
        headers = values[0]
        logger.info(f"📊 DIQQAT! Drivers listi shapkasi: {headers}")
        
        idx_car = fuzzy_match_header(headers, ['car_number', 'moshina', 'nomer', 'car'])
        idx_name = fuzzy_match_header(headers, ['driver_name', 'ism', 'haydovchi', 'name'])
        idx_tid = fuzzy_match_header(headers, ['driver_user_id', 'tg_id', 'telegram_id', 'id'])
        
        if idx_car == -1 or idx_tid == -1:
            logger.error(f"❌ XATO: Kerakli ustunlar topilmadi. Car idx: {idx_car}, TID idx: {idx_tid}")
            return {}
            
        drivers = {}
        for row in values[1:]:
            if len(row) <= max(idx_car, idx_tid): continue
            norm_car = normalize_car(row[idx_car])
            tid = clean_tid(row[idx_tid])
            if norm_car and tid:
                drivers[norm_car] = {
                    'driver_name': row[idx_name].strip() if idx_name != -1 and len(row) > idx_name else "Haydovchi",
                    'telegram_id': tid
                }
        
        logger.info(f"✅ Topilgan haydovchilar moshinalari: {list(drivers.keys())}")
        return drivers
    except Exception as e:
        logger.error(f"Error in get_drivers: {e}")
        return {}

def get_new_orders():
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{ORDERS_SHEET_NAME}!A1:Z500').execute()
        values = result.get('values', [])
        if not values: return []
        
        headers = values[0]
        idx_id = fuzzy_match_header(headers, ['order_id', 'id', 'nomer'])
        idx_car = fuzzy_match_header(headers, ['car_number', 'moshina', 'car'])
        idx_status = fuzzy_match_header(headers, ['status', 'holat'])
        
        # Other fields for data
        idx_addr = fuzzy_match_header(headers, ['address', 'manzil'])
        idx_cargo = fuzzy_match_header(headers, ['cargo', 'yuk'])
        idx_comment = fuzzy_match_header(headers, ['comment', 'izoh'])
        
        new_orders = []
        for i, row in enumerate(values[1:]):
            if not any(row): continue
            status = row[idx_status].strip().upper() if idx_status != -1 and len(row) > idx_status else ""
            if status in ['', 'NEW', 'YANGI']:
                order_id = row[idx_id].strip() if idx_id != -1 and len(row) > idx_id else str(i+1)
                car_num = normalize_car(row[idx_car]) if idx_car != -1 and len(row) > idx_car else ""
                if order_id and car_num:
                    new_orders.append({
                        'row_index': i + 2,
                        'order_id': order_id,
                        'car_number': car_num,
                        'address': row[idx_addr].strip() if idx_addr != -1 and len(row) > idx_addr else "",
                        'cargo': row[idx_cargo].strip() if idx_cargo != -1 and len(row) > idx_cargo else "",
                        'comment': row[idx_comment].strip() if idx_comment != -1 and len(row) > idx_comment else ""
                    })
        return new_orders
    except Exception as e:
        logger.error(f"Error in get_new_orders: {e}")
        return []

def update_order_status(row_index: int, status: str):
    sheets = get_sheets_service()
    if not sheets: return
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{ORDERS_SHEET_NAME}!1:1').execute()
        headers = result.get('values', [[]])[0]
        idx = fuzzy_match_header(headers, ['status', 'holat'])
        if idx == -1: return
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
    sheets = get_sheets_service()
    if not sheets: return
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A1:Z100').execute()
        values = result.get('values', [])
        if not values: return
        headers = values[0]
        idx_car = fuzzy_match_header(headers, ['car_number', 'moshina'])
        idx_status = fuzzy_match_header(headers, ['status', 'holat'])
        idx_order = fuzzy_match_header(headers, ['current_order_id', 'buyurtma'])
        
        norm_target = normalize_car(car_number)
        row_num = -1
        for i, row in enumerate(values[1:]):
            if len(row) > idx_car and normalize_car(row[idx_car]) == norm_target:
                row_num = i + 2
                break
        if row_num != -1:
            col_status = chr(ord('A') + idx_status) if idx_status != -1 else ""
            col_order = chr(ord('A') + idx_order) if idx_order != -1 else ""
            data = []
            if col_status: data.append({'range': f'{DRIVERS_SHEET_NAME}!{col_status}{row_num}', 'values': [[status]]})
            if col_order: data.append({'range': f'{DRIVERS_SHEET_NAME}!{col_order}{row_num}', 'values': [[current_order_id]]})
            if data:
                sheets.values().batchUpdate(spreadsheetId=GOOGLE_SHEET_ID, body={'valueInputOption': 'USER_ENTERED', 'data': data}).execute()
    except Exception as e:
        logger.error(f"Error updating driver status: {e}")

def get_driver_by_tid(tid: int):
    sheets = get_sheets_service()
    if not sheets: return None
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A1:Z100').execute()
        values = result.get('values', [])
        if not values: return None
        headers = values[0]
        idx_car = fuzzy_match_header(headers, ['car_number', 'moshina'])
        idx_name = fuzzy_match_header(headers, ['driver_name', 'ism'])
        idx_tid = fuzzy_match_header(headers, ['driver_user_id', 'id'])
        idx_status = fuzzy_match_header(headers, ['status', 'holat'])
        idx_order = fuzzy_match_header(headers, ['current_order_id', 'buyurtma'])
        
        for row in values[1:]:
            if len(row) > idx_tid and clean_tid(row[idx_tid]) == tid:
                return {
                    'car_number': row[idx_car].strip() if idx_car != -1 and len(row) > idx_car else '',
                    'driver_name': row[idx_name].strip() if idx_name != -1 and len(row) > idx_name else '',
                    'status': row[idx_status].strip() if idx_status != -1 and len(row) > idx_status else 'BO\'SH',
                    'current_order_id': row[idx_order].strip() if idx_order != -1 and len(row) > idx_order else ''
                }
        return None
    except Exception: return None

def get_drivers_status_list():
    drivers = get_drivers() # This logs headers
    # Simplified version for the UI list
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A1:Z100').execute()
        values = result.get('values', [])
        headers = values[0]
        idx_car = fuzzy_match_header(headers, ['car_number', 'moshina'])
        idx_name = fuzzy_match_header(headers, ['driver_name', 'ism'])
        idx_status = fuzzy_match_header(headers, ['status', 'holat'])
        idx_order = fuzzy_match_header(headers, ['current_order_id', 'buyurtma'])
        
        res = []
        for row in values[1:]:
            if len(row) <= max(idx_car, idx_status): continue
            res.append({
                'car_number': row[idx_car].strip() if idx_car != -1 else '?',
                'driver_name': row[idx_name].strip() if idx_name != -1 else '?',
                'status': row[idx_status].strip() if idx_status != -1 else 'BO\'SH',
                'order_id': row[idx_order].strip() if idx_order != -1 else ''
            })
        return res
    except: return []

def get_all_drivers_list():
    drivers = get_drivers()
    return [(v['driver_name'], str(v['telegram_id'])) for v in drivers.values()]

def get_all_cars_list():
    drivers = get_drivers()
    return sorted(list(drivers.keys()))

import logging
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from core.config import GOOGLE_SERVICE_ACCOUNT_INFO, GOOGLE_SHEET_ID

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
    """Read driver list from DRIVERS sheet using header names.
    Expected headers: car_number, driver_name, driver_user_id, status, current_order_id
    """
    sheets = get_sheets_service()
    if not sheets: return {}
    
    try:
        result = sheets.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='DRIVERS!A1:Z'
        ).execute()
        values = result.get('values', [])
        if not values: return {}
        
        headers = [h.strip().lower() for h in values[0]]
        rows = values[1:]
        
        # Identify column indices
        try:
            idx_car = headers.index('car_number')
        except ValueError:
            logger.error("DRIVERS header missing: car_number")
            return {}
        try:
            idx_name = headers.index('driver_name')
        except ValueError:
            logger.error("DRIVERS header missing: driver_name")
            return {}
        try:
            idx_tid = headers.index('driver_user_id')
        except ValueError:
            logger.error("DRIVERS header missing: driver_user_id")
            return {}
            
        drivers = {}
        for i, row in enumerate(rows):
            row_num = i + 2
            if len(row) <= max(idx_car, idx_name, idx_tid):
                continue
                
            raw_car = row[idx_car]
            raw_name = row[idx_name]
            raw_tid = row[idx_tid]
            
            norm_car = normalize_car(raw_car)
            parsed_tid = clean_tid(raw_tid)
            
            logger.info(f"DRIVERS row {row_num}: raw_car='{raw_car}', raw_tid='{raw_tid}', norm_car='{norm_car}', parsed_tid={parsed_tid}")
            
            if not norm_car:
                continue
                
            if parsed_tid is None:
                logger.error(f"Invalid driver_user_id row {row_num} car {raw_car} value='{raw_tid}'")
                continue
                
            drivers[norm_car] = {
                'driver_name': raw_name.strip(),
                'telegram_id': parsed_tid
            }
            
        return drivers
    except Exception as e:
        logger.error(f"Error getting drivers: {e}")
        return {}

def get_new_orders():
    """Read orders from ORDERS sheet using headers.
    Headers: order_id, car_number, address, cargo, comment, status
    """
    sheets = get_sheets_service()
    if not sheets: return []
    
    try:
        result = sheets.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='ORDERS!A1:Z'
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
            if len(row) <= idx_status:
                continue
                
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
    """Update status in ORDERS sheet. We assume status is the last column or we find it."""
    sheets = get_sheets_service()
    if not sheets: return
    
    try:
        # To be safe, we always find the status column first
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range='ORDERS!1:1').execute()
        headers = [h.strip().lower() for h in result.get('values', [[]])[0]]
        try:
            idx = headers.index('status')
            col_letter = chr(ord('A') + idx)
            range_name = f'ORDERS!{col_letter}{row_index}'
            
            body = {'values': [[status]]}
            sheets.values().update(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=range_name,
                valueInputOption='USER_ENTERED',
                body=body
            ).execute()
            logger.info(f"Updated ORDERS row {row_index} status to {status}")
        except ValueError:
            logger.error("ORDERS header missing: status")
    except Exception as e:
        logger.error(f"Error updating order status: {e}")

def update_driver_status_sheet(car_number: str, status: str, current_order_id: str = ""):
    """Update driver status in DRIVERS sheet."""
    sheets = get_sheets_service()
    if not sheets: return
    
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range='DRIVERS!A1:Z').execute()
        values = result.get('values', [])
        if not values: return
        
        headers = [h.strip().lower() for h in values[0]]
        rows = values[1:]
        
        try:
            idx_car = headers.index('car_number')
            idx_status = headers.index('status')
            idx_order = headers.index('current_order_id')
        except ValueError as e:
            logger.error(f"DRIVERS header missing: {e}")
            return
            
        norm_target = normalize_car(car_number)
        row_num = -1
        for i, row in enumerate(rows):
            if len(row) > idx_car and normalize_car(row[idx_car]) == norm_target:
                row_num = i + 2
                break
                
        if row_num == -1:
            logger.warning(f"Car {car_number} not found in DRIVERS sheet")
            return
            
        # Update status and order id
        col_status = chr(ord('A') + idx_status)
        col_order = chr(ord('A') + idx_order)
        
        # Batch update or individual
        sheets.values().update(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f'DRIVERS!{col_status}{row_num}',
            valueInputOption='USER_ENTERED',
            body={'values': [[status]]}
        ).execute()
        sheets.values().update(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f'DRIVERS!{col_order}{row_num}',
            valueInputOption='USER_ENTERED',
            body={'values': [[current_order_id]]}
        ).execute()
        
        logger.info(f"DRIVERS status changed: {car_number} -> {status}")
    except Exception as e:
        logger.error(f"Error updating DRIVERS sheet: {e}")

def get_all_drivers_list():
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range='DRIVERS!A1:Z').execute()
        values = result.get('values', [])
        if not values: return []
        
        headers = [h.strip().lower() for h in values[0]]
        try:
            idx_tid = headers.index('driver_user_id')
            idx_name = headers.index('driver_name')
        except ValueError: return []
        
        drivers = []
        seen = set()
        for row in values[1:]:
            if len(row) > max(idx_tid, idx_name):
                tid = clean_tid(row[idx_tid])
                name = row[idx_name].strip()
                if tid and tid not in seen:
                    drivers.append((name, str(tid)))
                    seen.add(tid)
        return drivers
    except Exception: return []

def get_all_cars_list():
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range='DRIVERS!A1:Z').execute()
        values = result.get('values', [])
        if not values: return []
        headers = [h.strip().lower() for h in values[0]]
        try:
            idx_car = headers.index('car_number')
        except ValueError: return []
        
        cars = set()
        for row in values[1:]:
            if len(row) > idx_car:
                norm = normalize_car(row[idx_car])
                if norm: cars.add(norm)
        return sorted(list(cars))
    except Exception: return []

def get_drivers_status():
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        # Just return the values, dashboard will handle it
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range='DRIVERS!A2:Z').execute()
        return result.get('values', [])
    except Exception: return []

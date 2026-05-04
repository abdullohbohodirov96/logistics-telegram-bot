import logging
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

def get_drivers():
    """Read driver list from DRIVERS sheet.
    Columns: A=car_number, B=driver_name, C=driver_user_id, D=status, E=current_order_id, F=updated_at
    """
    sheets = get_sheets_service()
    if not sheets: return {}
    
    try:
        result = sheets.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='DRIVERS!A2:C'
        ).execute()
        values = result.get('values', [])
        
        drivers = {}
        for row in values:
            if len(row) >= 3:
                car_number = row[0].strip()
                driver_name = row[1].strip()
                try:
                    telegram_id = int(row[2].strip())
                    drivers[car_number] = {
                        'driver_name': driver_name,
                        'telegram_id': telegram_id
                    }
                except ValueError:
                    logger.warning(f"Invalid telegram_id for driver {driver_name}")
        return drivers
    except Exception as e:
        logger.error(f"Error getting drivers: {e}")
        return {}

def get_new_orders():
    """Read new orders from ORDERS sheet.
    Columns: A=order_id, B=driver_user_id, C=driver_name, D=car_number, E=status
    We look for status = 'SEND' in column E.
    """
    sheets = get_sheets_service()
    if not sheets: return []
    
    try:
        result = sheets.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='ORDERS!A2:E'
        ).execute()
        values = result.get('values', [])
        
        new_orders = []
        for i, row in enumerate(values):
            if len(row) >= 5 and row[4].strip().upper() == 'SEND':
                order = {
                    'row_index': i + 2,
                    'order_id': row[0].strip(),
                    'car_number': row[3].strip(),
                    'driver_user_id': row[1].strip()
                }
                new_orders.append(order)
        return new_orders
    except Exception as e:
        logger.error(f"Error getting new orders: {e}")
        return []

def update_order_status(row_index: int, status: str):
    """Update status column (E) in ORDERS sheet."""
    sheets = get_sheets_service()
    if not sheets: return
    
    try:
        range_name = f'ORDERS!E{row_index}'
        body = {'values': [[status]]}
        sheets.values().update(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=range_name,
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        logger.info(f"Updated ORDERS row {row_index} status to {status}")
    except Exception as e:
        logger.error(f"Error updating order status in ORDERS sheet: {e}")

def update_driver_status_sheet(car_number: str, status: str, current_order_id: str = ""):
    """Update driver live status in DRIVERS sheet.
    Columns: D=status, E=current_order_id, F=updated_at
    """
    sheets = get_sheets_service()
    if not sheets: return
    
    try:
        from core.utils import get_now
        now_str = get_now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Find the car row
        result = sheets.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='DRIVERS!A:A'
        ).execute()
        values = result.get('values', [])
        
        row_idx = -1
        for i, row in enumerate(values):
            if i == 0: continue
            if row and row[0].strip().upper().replace(' ', '') == car_number.strip().upper().replace(' ', ''):
                row_idx = i + 1
                break
        
        if row_idx == -1:
            logger.warning(f"Car {car_number} not found in DRIVERS sheet")
            return
        
        body = {'values': [[status, current_order_id, now_str]]}
        sheets.values().update(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f'DRIVERS!D{row_idx}:F{row_idx}',
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        logger.info(f"DRIVERS status changed: {car_number} -> {status}")
    except Exception as e:
        logger.error(f"Error updating DRIVERS sheet: {e}")

def append_order_to_history_sheet(order_data: dict):
    """Append finished order to ORDERS sheet for history/mirror.
    Columns: order_id, driver_user_id, driver_name, car_number, status, start_time, transit_exists, completed_at, duration_minutes, created_at
    """
    sheets = get_sheets_service()
    if not sheets: return
    
    try:
        row = [
            order_data.get('order_id', ''),
            order_data.get('driver_user_id', ''),
            order_data.get('driver_name', ''),
            order_data.get('car_number', ''),
            order_data.get('status', 'DONE'),
            order_data.get('start_time', ''),
            str(order_data.get('transit_exists', False)),
            order_data.get('completed_at', ''),
            order_data.get('duration_minutes', ''),
            order_data.get('created_at', '')
        ]
        
        sheets.values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='ORDERS!A2',
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body={'values': [row]}
        ).execute()
        logger.info(f"Order {order_data.get('order_id')} appended to ORDERS sheet")
    except Exception as e:
        logger.error(f"Error appending to ORDERS sheet: {e}")

def get_all_drivers_list():
    """Returns list of unique drivers [(name, tid), ...] from DRIVERS sheet."""
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range='DRIVERS!B2:C').execute()
        rows = result.get('values', [])
        drivers = []
        seen = set()
        for row in rows:
            if len(row) >= 2:
                name, tid = row[0].strip(), row[1].strip()
                if tid not in seen:
                    drivers.append((name, tid))
                    seen.add(tid)
        return drivers
    except Exception as e:
        logger.error(f"Error loading DRIVERS: {e}")
        return []

def get_all_cars_list():
    """Returns list of unique car numbers from DRIVERS sheet."""
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range='DRIVERS!A2:A').execute()
        rows = result.get('values', [])
        return sorted(list(set(row[0].strip() for row in rows if row)))
    except Exception as e:
        logger.error(f"Error loading cars from DRIVERS: {e}")
        return []

def get_drivers_status():
    """Returns all rows from DRIVERS sheet for dashboard/admin."""
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range='DRIVERS!A2:F').execute()
        return result.get('values', [])
    except Exception as e:
        logger.error(f"Error getting drivers status from sheet: {e}")
        return []

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
    Columns: A=driver_user_id, B=driver_name, C=car_number, D=status, E=current_order_id
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
                try:
                    telegram_id = int(row[0].strip())
                    driver_name = row[1].strip()
                    car_number = row[2].strip()
                    # We index by car_number for internal bot logic
                    drivers[car_number] = {
                        'driver_name': driver_name,
                        'telegram_id': telegram_id
                    }
                except ValueError:
                    logger.warning(f"Invalid telegram_id in DRIVERS sheet")
        return drivers
    except Exception as e:
        logger.error(f"Error getting drivers: {e}")
        return {}

def get_new_orders():
    """Read orders from ORDERS sheet.
    Columns: A=order_id, B=car_number, C=address, D=cargo, E=comment, F=status
    """
    sheets = get_sheets_service()
    if not sheets: return []
    
    try:
        result = sheets.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='ORDERS!A2:F'
        ).execute()
        values = result.get('values', [])
        
        new_orders = []
        for i, row in enumerate(values):
            if len(row) >= 6 and row[5].strip().upper() == 'SEND':
                order = {
                    'row_index': i + 2,
                    'order_id': row[0].strip(),
                    'car_number': row[1].strip(),
                    'address': row[2].strip() if len(row) > 2 else '',
                    'cargo': row[3].strip() if len(row) > 3 else '',
                    'comment': row[4].strip() if len(row) > 4 else ''
                }
                new_orders.append(order)
        return new_orders
    except Exception as e:
        logger.error(f"Error getting new orders: {e}")
        return []

def update_order_status(row_index: int, status: str):
    """Update status column (F) in ORDERS sheet."""
    sheets = get_sheets_service()
    if not sheets: return
    
    try:
        range_name = f'ORDERS!F{row_index}'
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
    Columns: A=driver_user_id, B=driver_name, C=car_number, D=status, E=current_order_id
    """
    sheets = get_sheets_service()
    if not sheets: return
    
    try:
        # Find the car row by C column
        result = sheets.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='DRIVERS!C:C'
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
        
        body = {'values': [[status, current_order_id]]}
        sheets.values().update(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f'DRIVERS!D{row_idx}:E{row_idx}',
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        logger.info(f"DRIVERS status changed: {car_number} -> {status}")
    except Exception as e:
        logger.error(f"Error updating DRIVERS sheet: {e}")

def get_all_drivers_list():
    """Returns list of unique drivers [(name, tid), ...] from DRIVERS sheet."""
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range='DRIVERS!A2:B').execute()
        rows = result.get('values', [])
        drivers = []
        seen = set()
        for row in rows:
            if len(row) >= 2:
                tid, name = row[0].strip(), row[1].strip()
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
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range='DRIVERS!C2:C').execute()
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
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range='DRIVERS!A2:E').execute()
        return result.get('values', [])
    except Exception as e:
        logger.error(f"Error getting drivers status from sheet: {e}")
        return []

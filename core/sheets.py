import logging
from google.oauth2 import service_account
from googleapiclient.discovery import build
from core.config import GOOGLE_SERVICE_ACCOUNT_INFO, GOOGLE_SHEET_ID
from core.db import upsert_driver_status

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
    """Read driver list from drivers sheet.
    Columns: A=car_number, B=driver_name, C=telegram_id, D=status, E=current_order_id, F=started_at, G=updated_at
    """
    sheets = get_sheets_service()
    if not sheets: return {}
    
    try:
        result = sheets.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='drivers!A2:C'
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
    sheets = get_sheets_service()
    if not sheets: return []
    
    try:
        result = sheets.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='orders!A2:F'
        ).execute()
        values = result.get('values', [])
        
        new_orders = []
        
        for i, row in enumerate(values):
            # A: order_id, B: car_number, C: address, D: cargo, E: comment, F: status
            if len(row) >= 6 and row[5].strip().upper() == 'SEND':
                order = {
                    'row_index': i + 2, # +2 because 1-based and A2 is start
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
    sheets = get_sheets_service()
    if not sheets: return
    
    try:
        range_name = f'orders!F{row_index}'
        body = {
            'values': [[status]]
        }
        sheets.values().update(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=range_name,
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        logger.info(f"Updated row {row_index} status to {status}")
    except Exception as e:
        logger.error(f"Error updating order status for row {row_index}: {e}")

def update_driver_status_sheet(car_number: str, driver_name: str, telegram_id: int, status: str, current_order_id: str):
    """Update car status directly in drivers sheet (columns D-G).
    
    drivers sheet layout:
    A=car_number | B=driver_name | C=telegram_id | D=status | E=current_order_id | F=started_at | G=updated_at
    
    Bot calls this with:
      status='BAND', current_order_id='ORD-123' when delivery starts
      status="BO'SH", current_order_id='' when delivery finishes
    """
    sheets = get_sheets_service()
    if not sheets: return
    
    try:
        from datetime import datetime
        import pytz
        tz = pytz.timezone('Asia/Tashkent')
        now_str = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
        
        # Find the car row in drivers sheet
        result = sheets.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='drivers!A:G'
        ).execute()
        values = result.get('values', [])
        
        row_idx = -1
        for i, row in enumerate(values):
            if i == 0:
                continue  # skip header
            if len(row) > 0 and row[0].strip().upper().replace(' ', '') == car_number.strip().upper().replace(' ', ''):
                row_idx = i + 1  # 1-based
                break
        
        if row_idx == -1:
            logger.warning(f"Car {car_number} not found in drivers sheet, cannot update status")
            # Still upsert to Supabase
            upsert_driver_status(car_number, driver_name, telegram_id, status, current_order_id)
            return
        
        # Update columns D, E, F, G (status, current_order_id, started_at, updated_at)
        started_at = now_str if status == 'BAND' else ''
        
        body = {
            'values': [[status, current_order_id, started_at, now_str]]
        }
        sheets.values().update(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f'drivers!D{row_idx}:G{row_idx}',
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        
        logger.info(f"Updated drivers sheet row {row_idx}: {car_number} -> {status} / order={current_order_id}")
        
        # Also upsert to Supabase for history
        upsert_driver_status(car_number, driver_name, telegram_id, status, current_order_id)
        
    except Exception as e:
        logger.error(f"Error updating driver status in drivers sheet: {e}")

def get_drivers_status():
    """Read driver statuses from drivers sheet (columns A-G)."""
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range='drivers!A2:G'
        ).execute()
        return result.get('values', [])
    except Exception as e:
        logger.error(f"Error getting drivers status: {e}")
        return []

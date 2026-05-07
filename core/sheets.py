import logging
import os
import pickle
import json
import asyncio
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from core.config import GOOGLE_SHEET_ID, ORDERS_SHEET_NAME, DRIVERS_SHEET_NAME

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

def get_sheets_service():
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('credentials.json'):
                logger.error("credentials.json not found!")
                return None
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    return build('sheets', 'v4', credentials=creds).spreadsheets()

def fuzzy_match_header(headers, target_names):
    """Find column index by multiple possible header names (case insensitive)."""
    for i, h in enumerate(headers):
        clean_h = str(h).strip().lower()
        if clean_h in [t.lower() for t in target_names]:
            return i
    return -1

def col_to_letter(col_index):
    """Convert 0-based column index to Excel-style column letter (A, B, ..., AA, AB)."""
    letter = ''
    col_index += 1
    while col_index > 0:
        col_index, remainder = divmod(col_index - 1, 26)
        letter = chr(65 + remainder) + letter
    return letter

def get_new_orders():
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{ORDERS_SHEET_NAME}!A:Z').execute()
        values = result.get('values', [])
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
            if len(row) <= status_idx or not row[status_idx] or str(row[status_idx]).strip() == "":
                order_id = row[id_idx] if len(row) > id_idx else f"row_{i}"
                if not order_id: continue
                
                new_orders.append({
                    'row_index': i,
                    'order_id': str(order_id).strip(),
                    'car_number': str(row[car_idx]).strip().upper() if len(row) > car_idx else "",
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
    sheets = get_sheets_service()
    if not sheets: return {}
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A:E').execute()
        values = result.get('values', [])
        if not values or len(values) < 2: 
            logger.warning("Drivers sheet is empty or only has headers.")
            return {}

        headers = [str(h).strip().lower() for h in values[0]]
        drivers = {}
        for row in values[1:]:
            if len(row) < 3: continue
            car_raw = str(row[0]).strip().upper()
            if not car_raw: continue
            
            drivers[car_raw] = {
                'driver_name': row[1] if len(row) > 1 else "Noma'lum",
                'telegram_id': row[2] if len(row) > 2 else None,
                'status': row[3] if len(row) > 3 else "IDLE",
                'row_index': None
            }
        
        logger.info(f"Loaded drivers from sheet: {list(drivers.keys())}")
        return drivers
    except Exception as e:
        logger.error(f"Error fetching drivers from Sheets: {e}")
        return {}

def update_order_status(row_index, status):
    sheets = get_sheets_service()
    if not sheets: return
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{ORDERS_SHEET_NAME}!A1:Z1').execute()
        headers = result.get('values', [[]])[0]
        status_idx = fuzzy_match_header(headers, ['status', 'holat'])
        if status_idx == -1: return

        col_letter = col_to_letter(status_idx)
        sheets.values().update(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f'{ORDERS_SHEET_NAME}!{col_letter}{row_index}',
            valueInputOption='USER_ENTERED',
            body={'values': [[status]]}
        ).execute()
        logger.info(f"Sheets: Row {row_index} status updated to {status}")
    except Exception as e:
        logger.error(f"Error updating Sheets status: {e}")

def find_order_row(order_id: str) -> int:
    sheets = get_sheets_service()
    if not sheets: return -1
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{ORDERS_SHEET_NAME}!A:Z').execute()
        values = result.get('values', [])
        if not values: return -1
        headers = values[0]
        id_idx = fuzzy_match_header(headers, ['id', 'order_id', 'id_order'])
        if id_idx == -1: return -1
        for i, row in enumerate(values[1:], start=2):
            if len(row) > id_idx and str(row[id_idx]).strip() == str(order_id).strip():
                return i
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
    sheets = get_sheets_service()
    if not sheets: return
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A:A').execute()
        values = result.get('values', [])
        if not values: return
        
        target_car = str(car_number).strip().upper()
        row_idx = -1
        for i, row in enumerate(values):
            if row and str(row[0]).strip().upper() == target_car:
                row_idx = i + 1
                break
        
        if row_idx != -1:
            sheets.values().update(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f'{DRIVERS_SHEET_NAME}!D{row_idx}:E{row_idx}',
                valueInputOption='USER_ENTERED',
                body={'values': [[status, order_id]]}
            ).execute()
            logger.info(f"Driver {target_car} status updated to {status}")
    except Exception as e:
        logger.error(f"Error updating driver status: {e}")

def get_driver_by_tid(tid):
    sheets = get_sheets_service()
    if not sheets: return None
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A:E').execute()
        values = result.get('values', [])
        if not values: return None
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

# --- Admin Panel Functions ---

def get_drivers_status_list():
    """Returns a list of dicts for admin panel status view."""
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A:E').execute()
        values = result.get('values', [])
        if not values or len(values) < 2: return []
        
        data = []
        for row in values[1:]:
            if not row or len(row) < 1: continue
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
    """Returns a list of tuples (name, telegram_id) for history filtering."""
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A:C').execute()
        values = result.get('values', [])
        if not values or len(values) < 2: return []
        
        drivers = []
        for row in values[1:]:
            if len(row) >= 3 and row[2]:
                drivers.append((row[1], row[2]))
        return drivers
    except Exception as e:
        logger.error(f"Error getting all drivers list: {e}")
        return []

def get_all_cars_list():
    """Returns a unique list of car numbers for history filtering."""
    sheets = get_sheets_service()
    if not sheets: return []
    try:
        result = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{DRIVERS_SHEET_NAME}!A:A').execute()
        values = result.get('values', [])
        if not values or len(values) < 2: return []
        
        cars = sorted(list(set([row[0].strip().upper() for row in values[1:] if row])))
        return cars
    except Exception as e:
        logger.error(f"Error getting all cars list: {e}")
        return []

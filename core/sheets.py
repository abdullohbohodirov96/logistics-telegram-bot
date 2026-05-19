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

def get_new_orders(sheet_name=None):
    """Read SEND-status orders from a specific sheet tab."""
    from core.config import ORDERS_SHEET_NAME
    client = get_gspread_client()
    if not client: return []
    target_sheet = sheet_name or ORDERS_SHEET_NAME
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(target_sheet)
        values = worksheet.get_all_values()

        if not values or len(values) < 2: return []

        headers = values[0]
        id_idx = fuzzy_match_header(headers, ['id', 'order_id', 'id_order'])
        car_idx = fuzzy_match_header(headers, ['car', 'car_number', 'mashina', 'moshina'])
        addr_idx = fuzzy_match_header(headers, ['address', 'manzil'])
        cargo_idx = fuzzy_match_header(headers, ['cargo', 'yuk'])
        status_idx = fuzzy_match_header(headers, ['status', 'holat'])
        comment_idx = fuzzy_match_header(headers, ['comment', 'izoh'])

        new_orders = []
        for i, row in enumerate(values[1:], start=2):
            status_val = row[status_idx].strip() if len(row) > status_idx else ""
            if status_val.upper() == "SEND":
                order_id = row[id_idx].strip() if len(row) > id_idx else f"row_{i}"
                new_orders.append({
                    'row_index': i,
                    'order_id': order_id,
                    'car_number': row[car_idx].strip().upper() if len(row) > car_idx else "",
                    'address': row[addr_idx] if len(row) > addr_idx else "-",
                    'cargo': row[cargo_idx] if len(row) > cargo_idx else "",
                    'comment': row[comment_idx] if comment_idx != -1 and len(row) > comment_idx else "",
                    'sheet_name': target_sheet,
                })
        return new_orders
    except Exception as e:
        logger.error(f"Error get_new_orders (sheet={target_sheet}): {e}")
        return []


def get_drivers():
    """
    Returns {car_number: {driver_name, telegram_id, filial, status}} from drivers sheet.
    Col layout: A=car, B=driver_name, C=telegram_id, D=filial, E=status, F=order_ids, G=count
    """
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
            # D (index 3) = filial, E (index 4) = status
            filial = row[3].strip() if len(row) > 3 else ""
            status = row[4].strip() if len(row) > 4 else "BO'SH"
            drivers[car_raw] = {
                'driver_name': row[1],
                'telegram_id': row[2],
                'filial': filial,   # "Shiribod" yoki "Qorasaroy"
                'status': status,
            }
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
        cell = worksheet.find(str(order_id).strip(), in_column=1)
        if cell:
            headers = worksheet.row_values(1)
            status_idx = fuzzy_match_header(headers, ['status', 'holat'])
            if status_idx != -1:
                worksheet.update_cell(cell.row, status_idx + 1, status)
    except Exception as e:
        logger.error(f"Error update_order_status_by_order_id: {e}")


def write_driver_order_count_to_orders_sheet(order_id, driver_active_count):
    """
    Writes driver's active order count to column G of the orders sheet.
    Called when a new order is dispatched to a driver.
    Format: '1 buyurtma' / '2 buyurtma' / '3 buyurtma'
    """
    client = get_gspread_client()
    if not client: return
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(ORDERS_SHEET_NAME)
        cell = worksheet.find(str(order_id).strip(), in_column=1)
        if cell:
            count_label = f"{driver_active_count} buyurtma"
            worksheet.update_cell(cell.row, 7, count_label)  # Column G = index 7
            logger.info(f"[ORDERS_SHEET] order={order_id} row={cell.row} col G → '{count_label}'")
    except Exception as e:
        logger.error(f"[ORDERS_SHEET] write_driver_order_count_to_orders_sheet error: {e}")

def update_driver_status_sheet(car_number, status, order_id=""):
    """
    Updates drivers sheet (new layout):
    - Col D (4) = filial  [READ ONLY — never overwrite]
    - Col E (5) = status  (BO'SH / YUK OGAN)
    - Col F (6) = active order_ids joined by '/'  e.g. 'S213/S2134'
    - Col G (7) = count   e.g. '2 ta'
    """
    if not car_number:
        logger.warning("[DRIVER_SHEET] car_number is empty, skipping update.")
        return False
    client = get_gspread_client()
    if not client:
        logger.error("[DRIVER_SHEET] No gspread client.")
        return False
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
        cell = worksheet.find(str(car_number).strip().upper(), in_column=1)
        if not cell:
            logger.warning(f"[DRIVER_SHEET] '{car_number}' NOT FOUND in Drivers sheet.")
            return False

        row = cell.row

        if status == "BO'SH" or not order_id:
            worksheet.update_cell(row, 5, status)   # E = status
            worksheet.update_cell(row, 6, "")        # F = order_ids cleared
            worksheet.update_cell(row, 7, "")        # G = count cleared
            logger.info(f"[DRIVER_SHEET] car={car_number} → BO'SH, cleared")
        else:
            # YUK OGAN — merge order_ids in col F (index 5)
            row_vals = worksheet.row_values(row)
            existing_raw = row_vals[5] if len(row_vals) > 5 else ""
            existing_ids = [x.strip() for x in existing_raw.split("/") if x.strip()]
            if str(order_id).strip() not in existing_ids:
                existing_ids.append(str(order_id).strip())

            joined = "/".join(existing_ids)
            count_str = f"{len(existing_ids)} ta"

            worksheet.update_cell(row, 5, status)    # E = status
            worksheet.update_cell(row, 6, joined)    # F = order_ids
            worksheet.update_cell(row, 7, count_str) # G = count
            logger.info(f"[DRIVER_SHEET] car={car_number} status={status} orders='{joined}' count={count_str}")

        return True
    except Exception as e:
        logger.error(f"[DRIVER_SHEET] Error updating {car_number}: {e}")
        return False



def remove_order_from_driver_sheet(car_number, finished_order_id):
    """
    Called on order finish: removes finished_order_id from col F (order_ids).
    If no more orders left → sets col E to BO'SH, clears F and G.
    """
    if not car_number or not finished_order_id:
        return False
    client = get_gspread_client()
    if not client: return False
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
        cell = worksheet.find(str(car_number).strip().upper(), in_column=1)
        if not cell:
            return False
        row = cell.row
        row_vals = worksheet.row_values(row)
        # F = index 5 → order_ids
        existing_raw = row_vals[5] if len(row_vals) > 5 else ""
        existing_ids = [x.strip() for x in existing_raw.split("/") if x.strip()]
        existing_ids = [x for x in existing_ids if x != str(finished_order_id).strip()]

        if existing_ids:
            joined = "/".join(existing_ids)
            count_str = f"{len(existing_ids)} ta"
            worksheet.update_cell(row, 5, "YUK OGAN") # E
            worksheet.update_cell(row, 6, joined)      # F
            worksheet.update_cell(row, 7, count_str)   # G
            logger.info(f"[DRIVER_SHEET] Removed {finished_order_id} from {car_number}. Remaining: {joined}")
        else:
            worksheet.update_cell(row, 5, "BO'SH")    # E
            worksheet.update_cell(row, 6, "")          # F
            worksheet.update_cell(row, 7, "")          # G
            logger.info(f"[DRIVER_SHEET] {car_number} has no more orders → BO'SH")
        return True
    except Exception as e:
        logger.error(f"[DRIVER_SHEET] remove_order_from_driver_sheet error for {car_number}: {e}")
        return False



def get_driver_by_tid(tid):
    """Find driver row by telegram_id. Returns car_number, name, filial, status, current_order_id."""
    client = get_gspread_client()
    if not client: return None
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
        values = worksheet.get_all_values()
        for row in values[1:]:
            if len(row) > 2 and str(row[2]) == str(tid):
                return {
                    'car_number':      row[0],
                    'driver_name':     row[1],
                    'filial':          row[3] if len(row) > 3 else "",  # D
                    'status':          row[4] if len(row) > 4 else "",  # E
                    'current_order_id': row[5] if len(row) > 5 else "", # F
                }
        return None
    except Exception as e:
        logger.error(f"Error get_driver_by_tid: {e}")
        return None


def get_drivers_status_list():
    """Returns list of all drivers with their current status for admin panel."""
    client = get_gspread_client()
    if not client: return []
    try:
        sh = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
        values = worksheet.get_all_values()
        if not values or len(values) < 2: return []
        result = []
        for r in values[1:]:
            if not r: continue
            result.append({
                'car_number':  r[0].strip().upper(),
                'driver_name': r[1] if len(r) > 1 else "",
                'filial':      r[3] if len(r) > 3 else "",  # D
                'status':      r[4] if len(r) > 4 else "BO'SH",  # E
                'order_id':    r[5] if len(r) > 5 else "",  # F
            })
        return result
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

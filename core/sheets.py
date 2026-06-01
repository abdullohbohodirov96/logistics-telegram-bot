import logging
import os
import json
import time
import gspread
from google.oauth2.service_account import Credentials
from core.config import GOOGLE_SHEET_ID, ORDERS_SHEET_NAME, DRIVERS_SHEET_NAME

logger = logging.getLogger(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

_GSPREAD_CLIENT = None


# ── Retry wrapper for 429 errors ──────────────────────────────────────────────

def _retry(fn, retries=4, base_delay=5):
    """Call fn(), retry on 429/APIError with exponential backoff."""
    for attempt in range(retries):
        try:
            return fn()
        except gspread.exceptions.APIError as e:
            status = e.response.status_code if hasattr(e, 'response') else 0
            if status == 429 and attempt < retries - 1:
                wait = base_delay * (2 ** attempt)
                logger.warning(f"[SHEETS] 429 Rate limit hit. Waiting {wait}s (attempt {attempt+1}/{retries})...")
                time.sleep(wait)
            else:
                raise
    return None


# ── Auth ──────────────────────────────────────────────────────────────────────

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
            logger.error("No Google credentials found!")
            return None
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        _GSPREAD_CLIENT = gspread.authorize(creds)
        logger.info(f"Google Sheets connected as: {creds_dict.get('client_email')}")
        return _GSPREAD_CLIENT
    except Exception as e:
        logger.error(f"Auth error: {e}")
        return None


def fuzzy_match_header(headers, target_names):
    for i, h in enumerate(headers):
        clean_h = str(h).strip().lower()
        if clean_h in [t.lower() for t in target_names]:
            return i
    return -1


# ── get_new_orders ────────────────────────────────────────────────────────────

def get_new_orders(sheet_name=None):
    """Read SEND-status orders from a specific sheet tab."""
    client = get_gspread_client()
    if not client:
        return []
    target_sheet = sheet_name or ORDERS_SHEET_NAME
    try:
        def _read():
            sh = client.open_by_key(GOOGLE_SHEET_ID)
            worksheet = sh.worksheet(target_sheet)
            return worksheet.get_all_values()

        values = _retry(_read)
        if not values or len(values) < 2:
            return []

        headers = values[0]
        id_idx     = fuzzy_match_header(headers, ['id', 'order_id', 'id_order'])
        car_idx    = fuzzy_match_header(headers, ['car', 'car_number', 'mashina', 'moshina'])
        addr_idx   = fuzzy_match_header(headers, ['address', 'manzil'])
        cargo_idx  = fuzzy_match_header(headers, ['cargo', 'yuk'])
        status_idx = fuzzy_match_header(headers, ['status', 'holat'])
        comment_idx= fuzzy_match_header(headers, ['comment', 'izoh'])

        new_orders = []
        for i, row in enumerate(values[1:], start=2):
            status_val = row[status_idx].strip() if len(row) > status_idx >= 0 else ""
            if status_val.upper() == "SEND":
                order_id = row[id_idx].strip() if id_idx >= 0 and len(row) > id_idx else f"row_{i}"
                new_orders.append({
                    'row_index':  i,
                    'order_id':   order_id,
                    'car_number': row[car_idx].strip().upper() if car_idx >= 0 and len(row) > car_idx else "",
                    'address':    row[addr_idx] if addr_idx >= 0 and len(row) > addr_idx else "-",
                    'cargo':      row[cargo_idx] if cargo_idx >= 0 and len(row) > cargo_idx else "",
                    'comment':    row[comment_idx] if comment_idx >= 0 and len(row) > comment_idx else "",
                    'sheet_name': target_sheet,
                })
        return new_orders
    except Exception as e:
        logger.error(f"Error get_new_orders (sheet={target_sheet}): {e}")
        return []


_DRIVERS_CACHE = {}
_DRIVERS_CACHE_TIME = 0

# ── get_drivers ───────────────────────────────────────────────────────────────

def get_drivers():
    """Returns {car_number: {driver_name, telegram_id, filial, status}}."""
    global _DRIVERS_CACHE, _DRIVERS_CACHE_TIME
    
    # Cache for 60 seconds to avoid massive 429 Rate Limits
    if _DRIVERS_CACHE and time.time() - _DRIVERS_CACHE_TIME < 60:
        return _DRIVERS_CACHE

    client = get_gspread_client()
    if not client:
        return {}
    try:
        def _read():
            sh = client.open_by_key(GOOGLE_SHEET_ID)
            worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
            return worksheet.get_all_values()

        values = _retry(_read)
        if not values or len(values) < 2:
            return {}

        drivers = {}
        for row in values[1:]:
            if len(row) < 3:
                continue
            car_raw = str(row[0]).strip().upper()
            if not car_raw:
                continue
            # filial ustuni (D) endi ishlatilmaydi — order qaysi sheetda
            # yozilgan bo'lsa, o'sha branch aniqlanadi scheduler orqali
            status = row[3].strip() if len(row) > 3 else "BO'SH"
            drivers[car_raw] = {
                'driver_name': row[1],
                'telegram_id': row[2],
                'status':      status,
            }
            
        _DRIVERS_CACHE = drivers
        _DRIVERS_CACHE_TIME = time.time()
        return drivers
    except Exception as e:
        logger.error(f"Error get_drivers: {e}")
        return _DRIVERS_CACHE or {}


# ── update_order_status (by row index) ───────────────────────────────────────

def update_order_status(row_index, status, sheet_name=None):
    """Update order status cell in the correct sheet tab."""
    client = get_gspread_client()
    if not client:
        return
    target_sheet = sheet_name or ORDERS_SHEET_NAME
    try:
        def _update():
            sh = client.open_by_key(GOOGLE_SHEET_ID)
            worksheet = sh.worksheet(target_sheet)
            headers = worksheet.row_values(1)
            status_idx = fuzzy_match_header(headers, ['status', 'holat'])
            if status_idx != -1:
                worksheet.update_cell(row_index, status_idx + 1, status)
                logger.info(f"[{target_sheet}] Row {row_index} → {status}")

        _retry(_update)
    except Exception as e:
        logger.error(f"Error update_order_status (sheet={target_sheet}): {e}")


# ── update_order_status_by_order_id ──────────────────────────────────────────

def update_order_status_by_order_id(order_id, status, sheet_name=None):
    """
    Update order status searching across all branch sheets.
    Uses batch_update to avoid extra API calls.
    """
    client = get_gspread_client()
    if not client:
        return
    try:
        from core.config import BRANCHES
        def _do():
            sh = client.open_by_key(GOOGLE_SHEET_ID)

            if sheet_name:
                sheets_to_try = [sheet_name]
            else:
                seen = set()
                sheets_to_try = []
                for b in BRANCHES.values():
                    s = b.get('orders_sheet', '')
                    if s and s not in seen:
                        sheets_to_try.append(s)
                        seen.add(s)

            for target_sheet in sheets_to_try:
                try:
                    worksheet = sh.worksheet(target_sheet)
                    cell = worksheet.find(str(order_id).strip(), in_column=1)
                    if cell:
                        headers = worksheet.row_values(1)
                        status_idx = fuzzy_match_header(headers, ['status', 'holat'])
                        if status_idx != -1:
                            worksheet.update_cell(cell.row, status_idx + 1, status)
                            logger.info(f"[{target_sheet}] order={order_id} → {status}")
                        return
                except Exception as e:
                    if "CellNotFound" not in str(type(e).__name__):
                        logger.warning(f"update_order_status_by_order_id: '{target_sheet}' error: {e}")

        _retry(_do)
    except Exception as e:
        logger.error(f"Error update_order_status_by_order_id: {e}")


# ── write_driver_order_count_to_orders_sheet ─────────────────────────────────

def write_driver_order_count_to_orders_sheet(order_id, driver_active_count):
    """
    Writes driver's active order count to column G of the correct orders sheet.
    Searches both Shiribod and Qorasaroy sheets.
    """
    client = get_gspread_client()
    if not client:
        return
    try:
        from core.config import BRANCHES

        def _do():
            sh = client.open_by_key(GOOGLE_SHEET_ID)
            seen = set()
            sheets_to_try = []
            for b in BRANCHES.values():
                s = b.get('orders_sheet', '')
                if s and s not in seen:
                    sheets_to_try.append(s)
                    seen.add(s)

            count_label = f"{driver_active_count} buyurtma"
            for target_sheet in sheets_to_try:
                try:
                    worksheet = sh.worksheet(target_sheet)
                    cell = worksheet.find(str(order_id).strip(), in_column=1)
                    if cell:
                        worksheet.update_cell(cell.row, 7, count_label)
                        logger.info(f"[{target_sheet}] order={order_id} col G → '{count_label}'")
                        return
                except Exception as e:
                    if "CellNotFound" not in str(type(e).__name__):
                        logger.warning(f"write_driver_order_count: '{target_sheet}' error: {e}")

        _retry(_do)
    except Exception as e:
        logger.error(f"write_driver_order_count_to_orders_sheet error: {e}")


# ── write_order_result_note ──────────────────────────────────────────────────

def write_order_result_note(row_index: int, note: str, sheet_name: str = None):
    """
    Writes a human-readable result/reason note to column H of the orders sheet.
    Col H is reserved for bot feedback (why not sent, duplicate, etc).
    """
    client = get_gspread_client()
    if not client:
        return
    target_sheet = sheet_name or ORDERS_SHEET_NAME
    try:
        def _do():
            sh = client.open_by_key(GOOGLE_SHEET_ID)
            worksheet = sh.worksheet(target_sheet)
            worksheet.update_cell(row_index, 8, note)
            logger.info(f"[{target_sheet}] Row {row_index} col H → '{note}'")
        _retry(_do)
    except Exception as e:
        logger.error(f"write_order_result_note error (sheet={target_sheet}): {e}")


# ── update_driver_status_sheet ────────────────────────────────────────────────

def update_driver_status_sheet(car_number, status, order_id=""):
    """
    Updates drivers sheet using batch_update (1 API call instead of 3).
    Col D=status, E=order_ids, F=count
    """
    if not car_number:
        logger.warning("[DRIVER_SHEET] car_number is empty, skipping.")
        return False
    client = get_gspread_client()
    if not client:
        return False
    try:
        def _do():
            sh = client.open_by_key(GOOGLE_SHEET_ID)
            worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
            cell = worksheet.find(str(car_number).strip().upper(), in_column=1)
            if not cell:
                logger.warning(f"[DRIVER_SHEET] '{car_number}' NOT FOUND.")
                return False

            row = cell.row

            if status == "BO'SH" or not order_id:
                # Single batch: clear D, E, F
                worksheet.batch_update([{
                    'range': f'D{row}:F{row}',
                    'values': [[status, "", ""]]
                }])
                logger.info(f"[DRIVER_SHEET] car={car_number} → BO'SH, cleared")
            else:
                # Read E first, then batch update D, E, F
                row_vals = worksheet.row_values(row)
                existing_raw = row_vals[4] if len(row_vals) > 4 else ""
                existing_ids = [x.strip() for x in existing_raw.split("/") if x.strip()]
                if str(order_id).strip() not in existing_ids:
                    existing_ids.append(str(order_id).strip())

                joined = "/".join(existing_ids)
                count_str = f"{len(existing_ids)} ta"

                worksheet.batch_update([{
                    'range': f'D{row}:F{row}',
                    'values': [[status, joined, count_str]]
                }])
                logger.info(f"[DRIVER_SHEET] car={car_number} status={status} orders='{joined}' count={count_str}")
            return True

        return _retry(_do) or False
    except Exception as e:
        logger.error(f"[DRIVER_SHEET] Error updating {car_number}: {e}")
        return False


# ── remove_order_from_driver_sheet ────────────────────────────────────────────

def remove_order_from_driver_sheet(car_number, finished_order_id):
    """
    Removes finished_order_id from driver's active orders.
    Uses batch_update (1 API call instead of 3).
    """
    if not car_number or not finished_order_id:
        return False
    client = get_gspread_client()
    if not client:
        return False
    try:
        def _do():
            sh = client.open_by_key(GOOGLE_SHEET_ID)
            worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
            cell = worksheet.find(str(car_number).strip().upper(), in_column=1)
            if not cell:
                return False

            row = cell.row
            row_vals = worksheet.row_values(row)
            existing_raw = row_vals[4] if len(row_vals) > 4 else ""
            existing_ids = [x.strip() for x in existing_raw.split("/") if x.strip()]
            existing_ids = [x for x in existing_ids if x != str(finished_order_id).strip()]

            if existing_ids:
                joined = "/".join(existing_ids)
                count_str = f"{len(existing_ids)} ta"
                worksheet.batch_update([{
                    'range': f'D{row}:F{row}',
                    'values': [["YUK OGAN", joined, count_str]]
                }])
                logger.info(f"[DRIVER_SHEET] Removed {finished_order_id} from {car_number}. Remaining: {joined}")
            else:
                worksheet.batch_update([{
                    'range': f'D{row}:F{row}',
                    'values': [["BO'SH", "", ""]]
                }])
                logger.info(f"[DRIVER_SHEET] {car_number} → BO'SH (no more orders)")
            return True

        return _retry(_do) or False
    except Exception as e:
        logger.error(f"[DRIVER_SHEET] remove_order error for {car_number}: {e}")
        return False


# ── cancel_order_in_sheets ───────────────────────────────────────────────────

def cancel_order_in_sheets(order_id: str) -> bool:
    """Sets order status to BEKOR_QILINDI in all branch order sheets."""
    client = get_gspread_client()
    if not client:
        return False
    try:
        from core.config import BRANCHES

        def _do():
            sh = client.open_by_key(GOOGLE_SHEET_ID)
            seen = set()
            for b in BRANCHES.values():
                s = b.get('orders_sheet', '')
                if not s or s in seen:
                    continue
                seen.add(s)
                try:
                    ws = sh.worksheet(s)
                    cell = ws.find(str(order_id).strip(), in_column=1)
                    if cell:
                        headers = ws.row_values(1)
                        status_idx = fuzzy_match_header(headers, ['status', 'holat'])
                        if status_idx != -1:
                            ws.update_cell(cell.row, status_idx + 1, 'BEKOR_QILINDI')
                            logger.info(f"[SHEETS] Order {order_id} → BEKOR_QILINDI in '{s}'")
                        return True
                except Exception as e:
                    logger.warning(f"cancel_order_in_sheets: sheet={s} err: {e}")
            return False

        return _retry(_do) or False
    except Exception as e:
        logger.error(f"cancel_order_in_sheets error: {e}")
        return False


# ── Helper read functions ─────────────────────────────────────────────────────

def get_driver_by_tid(tid):
    client = get_gspread_client()
    if not client:
        return None
    try:
        def _read():
            sh = client.open_by_key(GOOGLE_SHEET_ID)
            worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
            return worksheet.get_all_values()
        values = _retry(_read)
        for row in values[1:]:
            if len(row) > 2 and str(row[2]) == str(tid):
                return {
                    'car_number':       row[0],
                    'driver_name':      row[1],
                    'status':           row[3] if len(row) > 3 else "",
                    'current_order_id': row[4] if len(row) > 4 else "",
                }
        return None
    except Exception as e:
        logger.error(f"Error get_driver_by_tid: {e}")
        return None


def get_drivers_status_list():
    client = get_gspread_client()
    if not client:
        return []
    try:
        def _read():
            sh = client.open_by_key(GOOGLE_SHEET_ID)
            worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
            return worksheet.get_all_values()
        values = _retry(_read)
        if not values or len(values) < 2:
            return []
        result = []
        for r in values[1:]:
            if not r:
                continue
            result.append({
                'car_number':  r[0].strip().upper(),
                'driver_name': r[1] if len(r) > 1 else "",
                'status':      r[3] if len(r) > 3 else "BO'SH",
                'order_id':    r[4] if len(r) > 4 else "",
            })
        return result
    except Exception as e:
        logger.error(f"Error get_drivers_status_list: {e}")
        return []


def get_all_drivers_list():
    client = get_gspread_client()
    if not client:
        return []
    try:
        def _read():
            sh = client.open_by_key(GOOGLE_SHEET_ID)
            worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
            return worksheet.get_all_values()
        values = _retry(_read)
        return [(r[1], r[2]) for r in values[1:] if len(r) >= 3 and r[2]]
    except Exception as e:
        logger.error(f"Error get_all_drivers_list: {e}")
        return []


def get_all_cars_list():
    client = get_gspread_client()
    if not client:
        return []
    try:
        def _read():
            sh = client.open_by_key(GOOGLE_SHEET_ID)
            worksheet = sh.worksheet(DRIVERS_SHEET_NAME)
            return worksheet.get_all_values()
        values = _retry(_read)
        return sorted(list(set([r[0].strip().upper() for r in values[1:] if r and r[0]])))
    except Exception as e:
        logger.error(f"Error get_all_cars_list: {e}")
        return []

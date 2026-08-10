"""
Instant-dispatch webhook server.

Google Sheets has no native way to push a notification the moment a cell
changes, but a bound Apps Script (installable onEdit trigger) can call an
external URL via UrlFetchApp. This module is that external URL's receiver:
the moment a dispatcher flips an order row's status to SEND, Apps Script
POSTs {sheet_name, row_index} here, and we dispatch that ONE order right
away — instead of waiting for the periodic poll (check_sheets_job, which
still runs as a safety net for anything this webhook misses).

Runs in the SAME process as the Telegram bot (see main.py), on aiohttp
(already a dependency via aiogram), bound to $PORT — Render needs the bot's
service to be a "Web Service" (not "Background Worker") for this inbound
HTTP to reach it at all.
"""
import asyncio
import logging
from aiohttp import web

from core.config import SHEET_WEBHOOK_SECRET

logger = logging.getLogger(__name__)


def create_webhook_app(bot) -> web.Application:
    app = web.Application()

    async def health(request):
        return web.json_response({"status": "ok"})

    async def sheet_edit(request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        if not SHEET_WEBHOOK_SECRET or data.get("secret") != SHEET_WEBHOOK_SECRET:
            logger.warning("[WEBHOOK] Rejected request: bad/missing secret.")
            return web.json_response({"error": "unauthorized"}, status=401)

        sheet_name = (data.get("sheet_name") or "").strip()
        row_index = data.get("row_index")
        try:
            row_index = int(row_index)
        except (TypeError, ValueError):
            return web.json_response({"error": "row_index must be an int"}, status=400)

        if not sheet_name or row_index < 2:
            return web.json_response({"error": "missing sheet_name or bad row_index"}, status=400)

        logger.info(f"[WEBHOOK] SEND edit received: sheet='{sheet_name}' row={row_index}")

        # Respond immediately — Apps Script's UrlFetchApp has its own timeout,
        # and the dispatcher doesn't need to wait for the Telegram send to
        # finish. Processing continues in the background.
        asyncio.create_task(_handle_one_row(bot, sheet_name, row_index))
        return web.json_response({"status": "accepted"})

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/webhook/health", health)
    app.router.add_post("/webhook/sheet-edit", sheet_edit)
    return app


async def _handle_one_row(bot, sheet_name: str, row_index: int):
    from core.config import BRANCHES
    from core.sheets import get_order_row, get_drivers
    from core.scheduler import process_one_order

    order = await asyncio.to_thread(get_order_row, sheet_name, row_index)
    if not order:
        logger.info(
            f"[WEBHOOK] sheet='{sheet_name}' row={row_index}: not a SEND row "
            f"(already handled or edited to something else). Skipping."
        )
        return

    branch_name, group_id = None, None
    for name, cfg in BRANCHES.items():
        if cfg.get("orders_sheet") == sheet_name:
            branch_name, group_id = name, cfg.get("group_id")
            break
    if not branch_name:
        logger.error(f"[WEBHOOK] sheet='{sheet_name}' doesn't match any configured branch. Ignoring.")
        return

    drivers = await asyncio.to_thread(get_drivers)
    await process_one_order(bot, branch_name, sheet_name, group_id, order, drivers)

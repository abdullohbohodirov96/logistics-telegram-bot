import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.config import BOT_TOKEN, POLL_INTERVAL_SECONDS, is_sheets_configured, WEBHOOK_URL, PORT
from core.handlers import router

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def on_startup(bot: Bot):
    if WEBHOOK_URL:
        webhook_path = f"/webhook/{BOT_TOKEN.split(':')[1]}"
        full_url = f"{WEBHOOK_URL.rstrip('/')}{webhook_path}"
        logger.info(f"Setting webhook to: {full_url}")
        await bot.set_webhook(full_url, drop_pending_updates=True)
    else:
        logger.info("No WEBHOOK_URL provided. Deleting webhook and using polling...")
        await bot.delete_webhook(drop_pending_updates=True)

async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set. Exiting.")
        return

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    dp.startup.register(on_startup)

    # Start Google Sheets scheduler
    if is_sheets_configured():
        try:
            from core.scheduler import check_sheets_job
            logger.info(f"Starting Google Sheets polling job (interval: {POLL_INTERVAL_SECONDS}s)...")
            scheduler = AsyncIOScheduler()
            scheduler.add_job(check_sheets_job, 'interval', seconds=POLL_INTERVAL_SECONDS, args=[bot])
            scheduler.start()
        except Exception as e:
            logger.error(f"Failed to start Google Sheets scheduler: {e}")

    if WEBHOOK_URL:
        # WEBHOOK MODE
        webhook_path = f"/webhook/{BOT_TOKEN.split(':')[1]}"
        app = web.Application()
        
        # Simple health check
        async def handle_root(request): return web.json_response({"status": "ok", "mode": "webhook"})
        app.router.add_get("/", handle_root)
        
        handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
        handler.register(app, path=webhook_path)
        setup_application(app, dp, bot=bot)
        
        logger.info(f"Starting Webhook server on port {PORT}...")
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        
        # Keep alive
        while True: await asyncio.sleep(3600)
    else:
        # POLLING MODE
        logger.info("Starting Polling mode...")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot execution stopped.")

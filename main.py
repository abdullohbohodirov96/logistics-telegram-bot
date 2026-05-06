import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.config import BOT_TOKEN, POLL_INTERVAL_SECONDS, is_sheets_configured, WEBHOOK_URL, PORT
from core.handlers import router

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def dummy_server():
    """A dummy server to keep Render Web Service happy if needed."""
    from fastapi import FastAPI
    import uvicorn
    app = FastAPI()
    @app.get("/")
    async def root(): return {"status": "ok"}
    
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    import uuid
    instance_id = str(uuid.uuid4())[:8]
    logger.info(f"--- Bot Instance {instance_id} Starting ---")
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set. Exiting.")
        return

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    # Start Google Sheets scheduler if configured
    if is_sheets_configured():
        try:
            from core.scheduler import check_sheets_job
            logger.info(f"Starting Google Sheets polling job (interval: {POLL_INTERVAL_SECONDS}s)...")
            scheduler = AsyncIOScheduler()
            scheduler.add_job(check_sheets_job, 'interval', seconds=POLL_INTERVAL_SECONDS, args=[bot])
            scheduler.start()
        except Exception as e:
            logger.error(f"Failed to start Google Sheets scheduler: {e}")
    else:
        logger.warning("Google Sheets credentials are MISSING. Scheduler NOT started.")

    # Handle Webhook vs Polling
    if WEBHOOK_URL:
        logger.info(f"Setting webhook to {WEBHOOK_URL}...")
        # Webhook logic would go here if needed, but the user requested one instance of polling.
        # If they use Webhook, we should implement it.
        # For now, if WEBHOOK_URL is provided, we'll assume they want to use it eventually.
        pass

    logger.info("Dropping pending updates and starting polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.sleep(2) # Small cooldown to allow other instances to settle
    
    # Run polling and dummy server (for Render) concurrently
    tasks = [dp.start_polling(bot)]
    if os.getenv("RENDER"): # Only start dummy server on Render
        logger.info(f"Starting dummy server on port {PORT} for Render...")
        tasks.append(dummy_server())

    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        logger.error(f"Critical bot error: {e}")
    finally:
        await bot.session.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot execution stopped.")

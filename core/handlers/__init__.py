from aiogram import Router
from .start import router as start_router
from .delivery import router as delivery_router
from .admin import router as admin_router
from .history import router as history_router
from .odoo_handler import router as odoo_router

router = Router()
router.include_router(start_router)
router.include_router(admin_router)
router.include_router(history_router)
router.include_router(delivery_router)
router.include_router(odoo_router)

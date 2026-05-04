import logging
import xmlrpc.client
import asyncio
from core.config import ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_API_KEY, is_odoo_configured

logger = logging.getLogger(__name__)

class OdooClient:
    def __init__(self):
        self.url = ODOO_URL
        self.db = ODOO_DB
        self.username = ODOO_USERNAME
        self.password = ODOO_API_KEY
        self.uid = None

    async def authenticate(self):
        """Authenticate with Odoo and get UID with detailed logging."""
        if not is_odoo_configured():
            logger.error("ODOO env variables missing")
            return None
        
        common_url = f"{self.url}/xmlrpc/2/common"
        logger.info(f"Odoo Debug: URL={self.url}, DB={self.db}, Username={self.username}, API_KEY={'SET' if self.password else 'MISSING'}")
        logger.info(f"Odoo Debug: Target XML-RPC URL={common_url}")

        try:
            common = xmlrpc.client.ServerProxy(common_url, timeout=10)
            uid = await asyncio.to_thread(common.authenticate, self.db, self.username, self.password, {})
            
            if uid:
                self.uid = uid
                logger.info(f"Odoo Auth Success: UID={uid}")
                return uid
            else:
                logger.error("Odoo Auth Failed: common.authenticate returned False (Invalid credentials)")
                return None
        except Exception as e:
            logger.error(f"Odoo RPC Error during authentication: {str(e)}")
            return None

    async def get_delivery_orders(self, limit=10):
        """Get last N delivery orders from stock.picking."""
        if not self.uid:
            if not await self.authenticate():
                return []

        try:
            models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", timeout=15)
            domain = [('picking_type_code', '=', 'outgoing')]
            fields = ['id', 'name', 'partner_id', 'scheduled_date', 'state', 'origin']
            
            records = await asyncio.to_thread(
                models.execute_kw, self.db, self.uid, self.password,
                'stock.picking', 'search_read',
                [domain],
                {'fields': fields, 'limit': limit, 'order': 'id desc'}
            )
            
            logger.info(f"Odoo: {len(records)} delivery orders fetched")
            return records
        except Exception as e:
            logger.error(f"Error fetching Odoo delivery orders: {e}")
            return []

    async def test_connection(self):
        """Test connection and return detailed status message."""
        debug_info = f"Config: URL={'SET' if self.url else 'MISSING'}, DB={'SET' if self.db else 'MISSING'}, User={'SET' if self.username else 'MISSING'}, Key={'SET' if self.password else 'MISSING'}\n"
        debug_info += f"Target: {self.url}/xmlrpc/2/common\n"

        if not is_odoo_configured():
            return False, f"❌ Odoo variables missing!\n{debug_info}"
        
        try:
            uid = await self.authenticate()
            if uid:
                return True, f"✅ Odoo connected! UID: {uid}\n{debug_info}"
            else:
                return False, f"❌ Odoo authentication failed!\n{debug_info}"
        except Exception as e:
            return False, f"❌ Odoo Error: {str(e)}\n{debug_info}"

# Singleton instance
odoo_client = OdooClient()

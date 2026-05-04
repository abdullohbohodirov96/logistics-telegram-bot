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
        """Authenticate with Odoo and get UID."""
        if not is_odoo_configured():
            logger.error("ODOO env variables missing")
            return None
        
        try:
            common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", timeout=10)
            uid = await asyncio.to_thread(common.authenticate, self.db, self.username, self.password, {})
            if uid:
                self.uid = uid
                logger.info(f"Odoo authenticated successfully. UID: {uid}")
                return uid
            else:
                logger.error("Odoo authentication failed: Invalid credentials")
                return None
        except Exception as e:
            logger.error(f"Odoo connection error: {e}")
            return None

    async def get_delivery_orders(self, limit=10):
        """Get last N delivery orders from stock.picking."""
        if not self.uid:
            if not await self.authenticate():
                return []

        try:
            models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", timeout=15)
            # Filter: picking_type_code = outgoing
            # Sorting by id desc to get latest
            domain = [('picking_type_code', '=', 'outgoing')]
            fields = ['id', 'name', 'partner_id', 'scheduled_date', 'state', 'origin']
            
            records = await asyncio.to_thread(
                models.execute_kw, self.db, self.uid, self.password,
                'stock.picking', 'search_read',
                [domain],
                {'fields': fields, 'limit': limit, 'order': 'id desc'}
            )
            
            logger.info(f"Odoo: {len(records)} delivery orders received")
            return records
        except Exception as e:
            logger.error(f"Error fetching Odoo delivery orders: {e}")
            return []

    async def test_connection(self):
        """Test connection and return status/error."""
        if not is_odoo_configured():
            return False, "ODOO env variables missing"
        
        uid = await self.authenticate()
        if uid:
            return True, "✅ Odoo connected"
        else:
            return False, "❌ Odoo connection failed (Check credentials/URL)"

# Singleton instance
odoo_client = OdooClient()

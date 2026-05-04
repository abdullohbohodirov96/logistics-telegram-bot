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

    async def get_version(self):
        """Get Odoo server version."""
        try:
            common_url = f"{self.url}/xmlrpc/2/common"
            common = xmlrpc.client.ServerProxy(common_url, timeout=10)
            version = await asyncio.to_thread(common.version)
            return version
        except Exception as e:
            logger.error(f"Odoo Version Error: {e}")
            return None

    async def authenticate(self):
        """Authenticate with Odoo and get UID with detailed logging."""
        if not is_odoo_configured():
            logger.error("ODOO env variables missing")
            return None
        
        common_url = f"{self.url}/xmlrpc/2/common"
        key_len = len(self.password) if self.password else 0
        logger.info(f"Odoo Auth Attempt: URL={self.url}, DB={self.db}, User={self.username}, KeyLen={key_len}")

        try:
            common = xmlrpc.client.ServerProxy(common_url, timeout=10)
            uid = await asyncio.to_thread(common.authenticate, self.db, self.username, self.password, {})
            
            if uid:
                self.uid = uid
                logger.info(f"Odoo Auth Success: UID={uid}")
                return uid
            else:
                logger.error("Odoo Auth Failed: common.authenticate returned False")
                return False
        except Exception as e:
            logger.error(f"Odoo RPC Error during authentication: {str(e)}")
            raise e

    async def get_delivery_orders(self, limit=10):
        """Get last N delivery orders from stock.picking."""
        if not self.uid:
            # Try to authenticate first
            try:
                auth_res = await self.authenticate()
                if not auth_res: return []
            except: return []

        try:
            models_url = f"{self.url}/xmlrpc/2/object"
            models = xmlrpc.client.ServerProxy(models_url, timeout=15)
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
        """Perform comprehensive connection test and return report."""
        if not is_odoo_configured():
            return False, "❌ Odoo env variables missing (URL, DB, Username yoki Key yo'q)"
        
        report = f"🔍 **Odoo Test Hisoboti:**\n"
        report += f"🌐 URL: `{self.url}`\n"
        report += f"🗄 DB: `{self.db}`\n"
        report += f"👤 User: `{self.username}`\n"
        report += f"🔑 Key length: {len(self.password) if self.password else 0}\n\n"

        try:
            # 1. Check version
            version_info = await self.get_version()
            if version_info:
                v_str = version_info.get('server_version', 'Noma\'lum')
                report += f"✅ Server Connected. Version: {v_str}\n"
            else:
                report += f"❌ Server connection failed (Timeout/URL error)\n"
                return False, report

            # 2. Authenticate
            uid = await self.authenticate()
            if uid:
                report += f"✅ Odoo authenticated! UID: {uid}"
                return True, report
            else:
                report += f"❌ Auth failed: DB, email yoki API key noto'g'ri"
                return False, report
        except Exception as e:
            report += f"⚠️ RPC Error: {str(e)}"
            return False, report

# Singleton instance
odoo_client = OdooClient()

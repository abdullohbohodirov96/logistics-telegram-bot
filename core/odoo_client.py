import logging
import xmlrpc.client
import asyncio
import httpx
from core.config import ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_API_KEY, is_odoo_configured

logger = logging.getLogger(__name__)

class OdooClient:
    def __init__(self):
        self.url = ODOO_URL
        self.db = ODOO_DB
        self.username = ODOO_USERNAME
        self.password = ODOO_API_KEY
        self.uid = None
        self.session_id = None

    async def get_xmlrpc_version(self):
        """Test XML-RPC connection and get version."""
        try:
            common_url = f"{self.url}/xmlrpc/2/common"
            # Using 20s timeout as requested
            common = xmlrpc.client.ServerProxy(common_url, timeout=20)
            version = await asyncio.to_thread(common.version)
            return version
        except Exception as e:
            logger.error(f"Odoo XML-RPC Version Error ({type(e).__name__}): {e}")
            return None

    async def authenticate_xmlrpc(self):
        """Authenticate via XML-RPC."""
        try:
            common_url = f"{self.url}/xmlrpc/2/common"
            common = xmlrpc.client.ServerProxy(common_url, timeout=20)
            uid = await asyncio.to_thread(common.authenticate, self.db, self.username, self.password, {})
            if uid:
                self.uid = uid
                return uid
            return False
        except Exception as e:
            logger.error(f"Odoo XML-RPC Auth Error ({type(e).__name__}): {e}")
            return False

    async def authenticate_jsonrpc(self):
        """Authenticate via JSON-RPC (/web/session/authenticate)."""
        try:
            auth_url = f"{self.url}/web/session/authenticate"
            payload = {
                "jsonrpc": "2.0",
                "params": {
                    "db": self.db,
                    "login": self.username,
                    "password": self.password
                }
            }
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(auth_url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("result") and data["result"].get("uid"):
                        self.uid = data["result"]["uid"]
                        self.session_id = resp.cookies.get("session_id")
                        logger.info(f"Odoo JSON-RPC Auth Success: UID={self.uid}")
                        return self.uid
            return False
        except Exception as e:
            logger.error(f"Odoo JSON-RPC Auth Error ({type(e).__name__}): {e}")
            return False

    async def test_connection(self):
        """Detailed test for both XML-RPC and JSON-RPC."""
        if not is_odoo_configured():
            return False, "❌ Odoo env variables missing."

        report = f"🔍 **Odoo Connection Test (Timeout: 20s):**\n"
        report += f"🌐 URL: `{self.url}`\n\n"

        # 1. XML-RPC Test
        xml_v = await self.get_xmlrpc_version()
        if xml_v:
            report += f"✅ XML-RPC: Connected (v{xml_v.get('server_version')})\n"
            xml_auth = await self.authenticate_xmlrpc()
            report += f"   - Auth: {'✅ Success' if xml_auth else '❌ Failed'}\n"
        else:
            report += f"❌ XML-RPC: Unavailable or blocked (Timeout/Plan)\n"

        # 2. JSON-RPC Test
        json_auth = await self.authenticate_jsonrpc()
        if json_auth:
            report += f"✅ JSON-RPC: Auth Success (UID: {json_auth})\n"
        else:
            report += f"❌ JSON-RPC: Auth Failed\n"

        success = bool(self.uid)
        if success:
            report += f"\n🏆 **Final Status: Connected (UID: {self.uid})**"
        else:
            report += f"\n💀 **Final Status: Disconnected**"
            
        return success, report

    async def get_delivery_orders(self, limit=10):
        """Get delivery orders using available method."""
        if not self.uid:
            # Try JSON-RPC as fallback or primary if XML-RPC fails
            if not await self.authenticate_jsonrpc():
                if not await self.authenticate_xmlrpc():
                    return []

        try:
            # We use XML-RPC for data fetching if possible, 
            # as it's cleaner for generic search_read.
            # If XML-RPC is blocked, we might need a JSON-RPC search_read implementation.
            models_url = f"{self.url}/xmlrpc/2/object"
            models = xmlrpc.client.ServerProxy(models_url, timeout=20)
            domain = [('picking_type_code', '=', 'outgoing')]
            fields = ['id', 'name', 'partner_id', 'scheduled_date', 'state', 'origin']
            
            records = await asyncio.to_thread(
                models.execute_kw, self.db, self.uid, self.password,
                'stock.picking', 'search_read',
                [domain],
                {'fields': fields, 'limit': limit, 'order': 'id desc'}
            )
            return records
        except Exception as e:
            logger.error(f"Error fetching Odoo deliveries ({type(e).__name__}): {e}")
            return []

# Singleton
odoo_client = OdooClient()

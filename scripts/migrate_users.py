import asyncio
import logging
import os
from supabase import create_client, Client
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load env from parent dir if needed
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

async def migrate():
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("SUPABASE_URL or SUPABASE_KEY not found in .env")
        return

    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        # Check if users table exists by trying to select from it
        try:
            supabase.table('users').select('telegram_id', count='exact').limit(1).execute()
            logger.info("✅ 'users' table already exists.")
        except Exception:
            logger.info("Creating 'users' table...")
            # Note: Supabase Python SDK doesn't support raw SQL easily unless via RPC or Edge Functions.
            # We recommend running the SQL in Supabase Dashboard SQL Editor.
            print("\n" + "="*50)
            print("SUPABASE SQL MIGRATION REQUIRED")
            print("="*50)
            print("Please run the following SQL in your Supabase SQL Editor:\n")
            print("""
CREATE TABLE IF NOT EXISTS public.users (
    telegram_id bigint PRIMARY KEY,
    full_name text,
    username text,
    language text DEFAULT 'uz_latin',
    created_at timestamptz DEFAULT now()
);
            """)
            print("="*50 + "\n")

    except Exception as e:
        logger.error(f"Migration error: {e}")

if __name__ == "__main__":
    asyncio.run(migrate())

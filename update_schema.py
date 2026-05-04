import os
from supabase import create_client, Client
from dotenv import load_dotenv
load_dotenv()
supabase = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))
print("Please run this SQL manually in Supabase SQL Editor:")
print("""
ALTER TABLE orders ADD COLUMN IF NOT EXISTS start_time timestamptz;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS finish_time timestamptz;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS duration_minutes integer;
""")

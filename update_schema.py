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
ALTER TABLE orders ADD COLUMN IF NOT EXISTS transit_status text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS transit_at timestamptz;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS arrived_at timestamptz;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivered_location_at timestamptz;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivered_lat double precision;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivered_lng double precision;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS loaded_photo_file_id text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS loaded_photo_at timestamptz;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS act_photo_file_id text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS act_photo_at timestamptz;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS finished_at timestamptz;
""")

create table public.orders (
    id uuid primary key default gen_random_uuid(),
    order_id text unique,
    car_number text,
    driver_name text,
    driver_telegram_id bigint,
    address text,
    cargo text,
    comment text,
    current_status text,
    group_message_id bigint,
    created_at timestamptz default now(),
    completed_at timestamptz,
    start_time timestamptz,
    finish_time timestamptz,
    duration_minutes integer
);

create table public.order_steps (
    id uuid primary key default gen_random_uuid(),
    order_id text,
    step_name text,
    step_value text,
    time_text text,
    photo_file_id text,
    location_lat double precision,
    location_lng double precision,
    created_at timestamptz default now()
);

create table public.drivers_status (
    car_number text primary key,
    driver_name text,
    telegram_id bigint,
    status text,
    current_order_id text,
    updated_at timestamptz default now()
);

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

-- Performance indexes for fast filtering
create index idx_orders_driver_tid on public.orders(driver_telegram_id);
create index idx_orders_car_number on public.orders(car_number);
create index idx_orders_status on public.orders(current_status);
create index idx_orders_created_at on public.orders(created_at desc);

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

create index idx_steps_order_id on public.order_steps(order_id);

create table public.drivers_status (
    car_number text primary key,
    driver_name text,
    telegram_id bigint,
    status text,
    current_order_id text,
    updated_at timestamptz default now()
);

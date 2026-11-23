CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS trucks (
    id SERIAL PRIMARY KEY,
    traccar_device_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('light', 'heavy', 'flatbed'))
);

CREATE TABLE IF NOT EXISTS drivers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shifts (
    id SERIAL PRIMARY KEY,
    driver_id INTEGER NOT NULL REFERENCES drivers(id),
    truck_id INTEGER NOT NULL REFERENCES trucks(id),
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id BIGSERIAL PRIMARY KEY,
    truck_id INTEGER NOT NULL REFERENCES trucks(id),
    geom geometry(Point, 4326) NOT NULL,
    speed DOUBLE PRECISION,
    heading DOUBLE PRECISION,
    recorded_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (truck_id, recorded_at)
);

CREATE TABLE IF NOT EXISTS latest_positions (
    truck_id INTEGER PRIMARY KEY REFERENCES trucks(id),
    geom geometry(Point, 4326) NOT NULL,
    speed DOUBLE PRECISION,
    heading DOUBLE PRECISION,
    recorded_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS tow_calls (
    id SERIAL PRIMARY KEY,
    pickup_geom geometry(Point, 4326) NOT NULL,
    required_truck_type TEXT NOT NULL CHECK (required_truck_type IN ('light', 'heavy', 'flatbed')),
    status TEXT NOT NULL DEFAULT 'pending',
    assigned_truck_id INTEGER REFERENCES trucks(id),
    eta_seconds DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

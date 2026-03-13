-- Driver location tracking table
CREATE TABLE IF NOT EXISTS driver_locations (
    id SERIAL PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES backhaul_jobs(id) ON DELETE CASCADE,
    driver_id INTEGER NOT NULL,
    latitude NUMERIC(10, 7) NOT NULL,
    longitude NUMERIC(10, 7) NOT NULL,
    status VARCHAR(50) DEFAULT 'en_route_to_pickup',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_driver_locations_job ON driver_locations(job_id);
CREATE INDEX IF NOT EXISTS idx_driver_locations_updated ON driver_locations(updated_at DESC);

-- Add contact fields to existing tables
ALTER TABLE hauliers ADD COLUMN IF NOT EXISTS contact_name VARCHAR(255);
ALTER TABLE hauliers ADD COLUMN IF NOT EXISTS contact_phone VARCHAR(50);
ALTER TABLE hauliers ADD COLUMN IF NOT EXISTS driver_photo_url VARCHAR(500);

ALTER TABLE loaders ADD COLUMN IF NOT EXISTS contact_name VARCHAR(255);
ALTER TABLE loaders ADD COLUMN IF NOT EXISTS contact_phone VARCHAR(50);

-- Add tracking status to jobs
ALTER TABLE backhaul_jobs ADD COLUMN IF NOT EXISTS tracking_active BOOLEAN DEFAULT FALSE;
ALTER TABLE backhaul_jobs ADD COLUMN IF NOT EXISTS tracking_started_at TIMESTAMP;

-- Materials master schema (SQLite)
--
-- Design decisions (see README for the long version):
--   * materials uses a surrogate key (material_id) instead of DTF Part # directly,
--     because vendor sample materials exist before they have a Part #. dtf_part_num
--     is UNIQUE but nullable; a CHECK guarantees every material is identifiable by
--     either a Part # or a vendor code.
--   * Lots are one-to-many under materials: everything that changes per receiving
--     (lot numbers, dates, stock, price, location) lives here.
--   * price_per_kilo is recorded on each lot (history for free); the ETL refreshes
--     materials.current_price_per_kilo from the most recent lot as a convenience.
--   * The Locations cell in the source is free text and may hold several locations.
--     lots.locations_raw preserves it verbatim; lot_locations holds the parsed
--     individual locations where parsing succeeds.
--   * Dates are TEXT in ISO-8601 (yyyy-mm-dd). SQLite has no date type; ISO text
--     sorts and compares correctly and every client can read it.
--   * staging_inventory_raw mirrors the source sheet as untyped text. The ETL loads
--     it first, then transforms into the typed tables. The data-quality report runs
--     against staging, so bad source rows are visible even when they can't be loaded.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id     INTEGER PRIMARY KEY,
    canonical_name  TEXT NOT NULL UNIQUE
);

-- Maps every raw spelling seen in the source ("NutraSci", "Nutra Sci LLC")
-- to one canonical supplier. The ETL consults this; unmapped spellings are
-- auto-added as their own canonical supplier and flagged in the quality report.
CREATE TABLE IF NOT EXISTS supplier_aliases (
    alias        TEXT PRIMARY KEY,
    supplier_id  INTEGER NOT NULL REFERENCES suppliers(supplier_id)
);

CREATE TABLE IF NOT EXISTS materials (
    material_id             INTEGER PRIMARY KEY,
    dtf_part_num            TEXT UNIQUE,          -- NULL for vendor samples not yet in inventory
    vendor_code             TEXT,                 -- vendor's own code, decoded off the bottle
    material_name           TEXT NOT NULL,
    supplier_id             INTEGER REFERENCES suppliers(supplier_id),
    category                TEXT CHECK (category IN ('raw', 'flavor')),
    allergen                TEXT,
    current_price_per_kilo  REAL,                 -- derived by ETL from newest lot
    is_sample_only          INTEGER NOT NULL DEFAULT 0,  -- 1 = vendor sample, no company stock yet
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (dtf_part_num IS NOT NULL OR vendor_code IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_materials_name ON materials(material_name);

CREATE TABLE IF NOT EXISTS lots (
    lot_id            INTEGER PRIMARY KEY,
    material_id       INTEGER NOT NULL REFERENCES materials(material_id),
    dtf_lot_num       TEXT,
    supplier_lot_num  TEXT,     -- source column "Lot/Batch"
    receiving_date    TEXT,     -- ISO yyyy-mm-dd
    exp_date          TEXT,     -- ISO yyyy-mm-dd
    status            TEXT,
    start_day_stock   REAL,
    current_stock     REAL,
    price_per_kilo    REAL,
    total_cost        REAL,
    locations_raw     TEXT,     -- verbatim source cell
    ready_to_archive  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_lots_material ON lots(material_id);

CREATE TABLE IF NOT EXISTS lot_locations (
    lot_id    INTEGER NOT NULL REFERENCES lots(lot_id) ON DELETE CASCADE,
    location  TEXT NOT NULL,
    PRIMARY KEY (lot_id, location)
);

-- Raw landing zone: the source sheet, one column per sheet column, all TEXT.
-- ingested_at + source_row let the quality report point at exact sheet rows.
CREATE TABLE IF NOT EXISTS staging_inventory_raw (
    staging_id        INTEGER PRIMARY KEY,
    source_row        INTEGER,        -- 1-based row number in the source sheet
    receiving_date    TEXT,
    locations         TEXT,
    dtf_lot_num       TEXT,
    dtf_part_num      TEXT,
    status            TEXT,
    allergen          TEXT,
    material_name     TEXT,
    supplier_mfg      TEXT,
    lot_batch         TEXT,
    exp_date          TEXT,
    start_day_stock   TEXT,
    current_stock     TEXT,
    filter_moving     TEXT,
    check_cycle_count TEXT,
    category          TEXT,
    price_per_kilo    TEXT,
    total_cost        TEXT,
    ready_to_archive  TEXT,
    ingested_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Phase 5 (sample ingestion) will populate these; defined now so the data model
-- is complete: a sample uses many materials, a material appears in many samples.
CREATE TABLE IF NOT EXISTS samples (
    sample_id     INTEGER PRIMARY KEY,
    sample_name   TEXT NOT NULL,
    client        TEXT,
    request_date  TEXT,
    source_file   TEXT
);

CREATE TABLE IF NOT EXISTS sample_materials (
    sample_id    INTEGER NOT NULL REFERENCES samples(sample_id),
    material_id  INTEGER NOT NULL REFERENCES materials(material_id),
    amount       REAL,
    unit         TEXT,
    PRIMARY KEY (sample_id, material_id)
);

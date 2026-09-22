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

-- Phase E: the R&D lab's flavor sample catalog (a second, separate Google
-- Sheet — not the warehouse inventory, and not `samples` above, which is
-- client sample *requests*). One row per physical sample sitting in the
-- lab. Flat rather than split into a staging + typed pair like
-- materials/lots: there's no one-to-many relationship here to model, each
-- source row already *is* one sample. `source_row` still points back at the
-- sheet for the same reason staging_inventory_raw does — citing exact rows
-- in a quality report.
--
-- dtf_part_num is nullable and deliberately NOT a foreign key into
-- materials: most lab samples don't have one yet (they're samples-in-
-- waiting), and even once filled in, linking sample -> warehouse material
-- is a display-time join (matched by Part #, see queries.py), not a
-- structural relationship this table enforces.
--
-- rd_id is the sample's STABLE identity — a human-maintained "RD-ID" column
-- in the lab sheet (format RD-0000..RD-9999), its own R&D-owned namespace.
-- It is neither sample_code (vendor-owned, collides — not unique) nor
-- dtf_part_num (company-owned, mostly absent, and not ours to mint). The
-- loader upserts on it (ON CONFLICT(rd_id)), the way the materials ETL
-- upserts on dtf_part_num, so lab_sample_id stays stable across reloads and
-- a Phase F formula line can reference a sample without the next load
-- repointing it. A sample with a Part # carries both: rd_id is its identity,
-- the Part # an attribute and the warehouse-link key.
CREATE TABLE IF NOT EXISTS lab_samples (
    lab_sample_id           INTEGER PRIMARY KEY,
    rd_id                   TEXT UNIQUE,    -- stable R&D identity, RD-0000..RD-9999
    source_row              INTEGER,        -- 1-based row number in the source sheet
    vendor                  TEXT,
    flavor_name             TEXT,
    sample_code             TEXT,
    declaration_type        TEXT,           -- Natural, N&A, Artificial, WONF
    location_lab            TEXT,
    flavor_family           TEXT,
    category_subcategory    TEXT,
    usage_level             TEXT,
    dry_aroma_descriptors   TEXT,
    aroma_intensity         TEXT,           -- kept as text: sheet allows "3-4" as well as "3"
    top_note                TEXT,
    mid_palate_character    TEXT,
    finish_note              TEXT,
    off_note_tendency       TEXT,
    matrix_performance      TEXT,
    best_pairings           TEXT,
    tested_in               TEXT,
    allergens               TEXT,
    date_received            TEXT,          -- ISO yyyy-mm-dd
    price_per_kilo           REAL,
    dtf_part_num             TEXT,
    ingested_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_lab_samples_code ON lab_samples(sample_code);
CREATE INDEX IF NOT EXISTS idx_lab_samples_name ON lab_samples(flavor_name);
CREATE INDEX IF NOT EXISTS idx_lab_samples_part_num ON lab_samples(dtf_part_num);
CREATE INDEX IF NOT EXISTS idx_lab_samples_rd_id ON lab_samples(rd_id);

-- Phase F: the formula object. A formula is a recipe — a batch of material
-- amounts plus the header fields no catalog holds (batch size, a name, notes).
-- Each artifact the builder produces (flavor sheet, sample record, labels) is
-- one rendering of this one object.
--
-- This is the first data in the DB no loader can rebuild: materials and lots
-- come from the inventory sheet, lab_samples from the lab sheet, but a formula
-- is authored here and exists nowhere else. Two consequences the loaders must
-- respect (and tests pin): the full-reload ETL and the lab loader must NEVER
-- touch these two tables, and db/*.db stops being a disposable cache.
--
-- A line REFERENCES a row, it never copies the row's name or price: material_id
-- for an adopted warehouse material, rd_id for a lab sample (the rebuild-robust
-- handle — lab_sample_id is a full-reload surrogate, rd_id is the stable R&D
-- identity; see the lab_samples comment above). A corrected price or renamed
-- material then reaches every formula that uses it, resolved at read time. A
-- free-typed material line is deliberately impossible: that would put an
-- unbacked name/price in the DB — fabricated data by another route (SPEC §2).
CREATE TABLE IF NOT EXISTS formulas (
    formula_id   INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    batch_size   REAL,           -- header field no catalog holds
    batch_unit   TEXT,
    notes        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS formula_lines (
    formula_line_id  INTEGER PRIMARY KEY,
    formula_id       INTEGER NOT NULL REFERENCES formulas(formula_id) ON DELETE CASCADE,
    material_id      INTEGER REFERENCES materials(material_id),  -- adopted warehouse material
    rd_id            TEXT REFERENCES lab_samples(rd_id),         -- lab sample, stable handle
    amount           REAL,
    unit             TEXT,
    -- Exactly one reference per line: an adopted material XOR a lab sample.
    -- Both set, or neither, is a malformed line — reject it at write time
    -- rather than silently total the wrong price (or none). The two FKs above
    -- add the other half: an id that doesn't exist is rejected too, so a line
    -- can only point at a real, resolvable row.
    CHECK ((material_id IS NULL) <> (rd_id IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_formula_lines_formula ON formula_lines(formula_id);

-- Phase F: the Flavor Sheet — the first deliverable the formula work is built
-- backwards from (R&D fills one daily). One sheet is a header (customer,
-- product, quote ID, servings per flavor) plus any number of flavor profiles,
-- four to a printed page; each profile is a BASE amount plus material lines in
-- mg per serving. Grams per sample are never stored: they're mg x servings /
-- 1000, and the generated workbook computes them with the template's own
-- formula.
--
-- Authored data no loader can rebuild, exactly like formulas above: the ETL
-- and the lab loader must never touch these tables (tests pin it).
--
-- A line references its material the same way formula_lines does —
-- material_id for a warehouse material, rd_id for a lab sample — so a renamed
-- material reaches every sheet. Unlike formula_lines, a line may instead carry
-- a typed_name: a material in neither catalog yet. The user chose that on
-- 2026-09-22 because blocking a daily sheet on a catalog gap makes the tool
-- unusable; it's safe here because a flavor sheet carries no price, so a typed
-- name can't fabricate a cost. The UI marks typed lines "not in catalog".
CREATE TABLE IF NOT EXISTS flavor_sheets (
    flavor_sheet_id  INTEGER PRIMARY KEY,
    customer         TEXT,
    product          TEXT,
    quote_id         TEXT,
    servings         REAL,           -- servings made per flavor; drives every gram weight
    sample_prefix    TEXT,           -- start of the suggested Sample ID, e.g. SMPL
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS flavor_profiles (
    flavor_profile_id  INTEGER PRIMARY KEY,
    flavor_sheet_id    INTEGER NOT NULL REFERENCES flavor_sheets(flavor_sheet_id) ON DELETE CASCADE,
    position           INTEGER NOT NULL,   -- 1-based slot order; 1-4 page one, 5-8 page two, ...
    flavor_name        TEXT,
    sample_id          TEXT,
    base_mg            REAL                -- the BASE row, mg per serving
);

CREATE TABLE IF NOT EXISTS flavor_profile_lines (
    flavor_profile_line_id  INTEGER PRIMARY KEY,
    flavor_profile_id       INTEGER NOT NULL REFERENCES flavor_profiles(flavor_profile_id) ON DELETE CASCADE,
    position                INTEGER NOT NULL,   -- print order within the profile
    material_id             INTEGER REFERENCES materials(material_id),
    rd_id                   TEXT REFERENCES lab_samples(rd_id),
    typed_name              TEXT,
    mg_per_serving          REAL,
    -- Exactly one source per line: a warehouse material, a lab sample, or a
    -- typed name. Two, or none, is a malformed line.
    CHECK ((material_id IS NOT NULL) + (rd_id IS NOT NULL) + (typed_name IS NOT NULL) = 1)
);

CREATE INDEX IF NOT EXISTS idx_flavor_profiles_sheet ON flavor_profiles(flavor_sheet_id);
CREATE INDEX IF NOT EXISTS idx_flavor_profile_lines_profile ON flavor_profile_lines(flavor_profile_id);

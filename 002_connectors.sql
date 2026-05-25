-- ===========================================================================
-- 002_connectors.sql
-- KRAB connector framework — integrations, dedup tracking, sync runs.
-- Run after 001_company_brain.sql, on the same database.
-- ===========================================================================

DO $$ BEGIN
    CREATE TYPE integration_status AS ENUM
        ('pending','active','error','disconnected');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE sync_run_status AS ENUM ('running','completed','failed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- connector_integrations -----------------------------------------------------
CREATE TABLE IF NOT EXISTS connector_integrations (
    id                    SERIAL PRIMARY KEY,
    company_id            TEXT    NOT NULL,
    user_id               INTEGER NOT NULL,
    connector_type        TEXT    NOT NULL,
    display_name          TEXT,
    status                integration_status NOT NULL DEFAULT 'pending',
    access_token_enc      TEXT,
    refresh_token_enc     TEXT,
    expires_at            TIMESTAMPTZ,
    config                JSONB DEFAULT '{}'::jsonb,
    oauth_state           TEXT,
    last_sync_at          TIMESTAMPTZ,
    last_sync_status      TEXT,
    last_error            TEXT,
    sync_interval_minutes INTEGER NOT NULL DEFAULT 360,
    created_at            TIMESTAMPTZ DEFAULT now(),
    updated_at            TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_integration_company_type UNIQUE (company_id, connector_type)
);
CREATE INDEX IF NOT EXISTS ix_integ_company ON connector_integrations (company_id);
CREATE INDEX IF NOT EXISTS ix_integ_type    ON connector_integrations (connector_type);
CREATE INDEX IF NOT EXISTS ix_integ_status  ON connector_integrations (status);
CREATE INDEX IF NOT EXISTS ix_integ_state   ON connector_integrations (oauth_state);

-- synced_documents (dedup + chunk linkage) ----------------------------------
CREATE TABLE IF NOT EXISTS synced_documents (
    id             SERIAL PRIMARY KEY,
    integration_id INTEGER REFERENCES connector_integrations(id) ON DELETE CASCADE,
    company_id     TEXT NOT NULL,
    connector_type TEXT NOT NULL,
    external_id    TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    title          TEXT,
    source_url     TEXT,
    chunk_count    INTEGER DEFAULT 0,
    updated_at     TIMESTAMPTZ,
    synced_at      TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_synced_doc_ext UNIQUE (integration_id, external_id)
);
CREATE INDEX IF NOT EXISTS ix_synced_company ON synced_documents (company_id);
CREATE INDEX IF NOT EXISTS ix_synced_type    ON synced_documents (connector_type);

-- connector_sync_runs (observability) ---------------------------------------
CREATE TABLE IF NOT EXISTS connector_sync_runs (
    id                    SERIAL PRIMARY KEY,
    integration_id        INTEGER REFERENCES connector_integrations(id) ON DELETE CASCADE,
    company_id            TEXT NOT NULL,
    status                sync_run_status NOT NULL DEFAULT 'running',
    documents_fetched     INTEGER DEFAULT 0,
    documents_new         INTEGER DEFAULT 0,
    documents_updated     INTEGER DEFAULT 0,
    documents_unchanged   INTEGER DEFAULT 0,
    chunks_created        INTEGER DEFAULT 0,
    chunks_deleted        INTEGER DEFAULT 0,
    triggered_resynthesis BOOLEAN DEFAULT FALSE,
    error                 TEXT,
    started_at            TIMESTAMPTZ DEFAULT now(),
    finished_at           TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_syncrun_company ON connector_sync_runs (company_id);
CREATE INDEX IF NOT EXISTS ix_syncrun_integ   ON connector_sync_runs (integration_id);

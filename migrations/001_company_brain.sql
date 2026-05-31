-- ===========================================================================
-- 001_company_brain.sql
-- KRAB Company Brain — synthesis layer schema.
-- Run against the SAME Postgres database that holds users + knowledge_chunks.
-- Safe to run once; uses IF NOT EXISTS where possible. For real migrations,
-- prefer Alembic and autogenerate from procedure_models.py — this file is the
-- explicit equivalent for teams not using Alembic yet.
-- ===========================================================================

-- Enum types -----------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE procedure_status AS ENUM
        ('draft','needs_review','verified','stale','archived');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE autonomy_level AS ENUM
        ('human_required','suggest_only','autonomous');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE rule_type AS ENUM ('decision','guardrail','required_input');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE conflict_type AS ENUM
        ('value_mismatch','contradiction','outdated','ambiguous');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE conflict_status AS ENUM ('open','resolved','dismissed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE run_status AS ENUM ('running','completed','failed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- procedures -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS procedures (
    id                   SERIAL PRIMARY KEY,
    company_id           TEXT    NOT NULL,
    user_id              INTEGER NOT NULL,
    slug                 TEXT    NOT NULL,
    title                TEXT    NOT NULL,
    description          TEXT    NOT NULL,
    domain               TEXT,
    trigger_condition    TEXT,
    status               procedure_status NOT NULL DEFAULT 'draft',
    autonomy_level       autonomy_level   NOT NULL DEFAULT 'human_required',
    version              INTEGER NOT NULL DEFAULT 1,
    synthesis_confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    coverage_score       DOUBLE PRECISION NOT NULL DEFAULT 0,
    cohesion_score       DOUBLE PRECISION NOT NULL DEFAULT 0,
    execution_count      INTEGER NOT NULL DEFAULT 0,
    success_count        INTEGER NOT NULL DEFAULT 0,
    cluster_signature    TEXT,
    last_verified_at     TIMESTAMPTZ,
    created_at           TIMESTAMPTZ DEFAULT now(),
    updated_at           TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_procedure_company_slug UNIQUE (company_id, slug)
);
CREATE INDEX IF NOT EXISTS ix_proc_company        ON procedures (company_id);
CREATE INDEX IF NOT EXISTS ix_proc_user           ON procedures (user_id);
CREATE INDEX IF NOT EXISTS ix_proc_domain         ON procedures (domain);
CREATE INDEX IF NOT EXISTS ix_proc_status         ON procedures (status);
CREATE INDEX IF NOT EXISTS ix_proc_autonomy       ON procedures (autonomy_level);
CREATE INDEX IF NOT EXISTS ix_proc_signature      ON procedures (cluster_signature);
CREATE INDEX IF NOT EXISTS ix_proc_company_status ON procedures (company_id, status);

-- procedure_steps ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS procedure_steps (
    id               SERIAL PRIMARY KEY,
    procedure_id     INTEGER REFERENCES procedures(id) ON DELETE CASCADE,
    step_order       INTEGER NOT NULL,
    instruction      TEXT    NOT NULL,
    source_chunk_ids INTEGER[] DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_step_procedure ON procedure_steps (procedure_id);

-- procedure_rules ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS procedure_rules (
    id               SERIAL PRIMARY KEY,
    procedure_id     INTEGER REFERENCES procedures(id) ON DELETE CASCADE,
    rule_type        rule_type NOT NULL,
    content          TEXT NOT NULL,
    source_chunk_ids INTEGER[] DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_rule_procedure ON procedure_rules (procedure_id);

-- procedure_sources (provenance) --------------------------------------------
CREATE TABLE IF NOT EXISTS procedure_sources (
    id             SERIAL PRIMARY KEY,
    procedure_id   INTEGER REFERENCES procedures(id) ON DELETE CASCADE,
    chunk_id       INTEGER REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
    relevance      DOUBLE PRECISION DEFAULT 0,
    contributed_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_proc_source UNIQUE (procedure_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS ix_psource_procedure ON procedure_sources (procedure_id);
CREATE INDEX IF NOT EXISTS ix_psource_chunk     ON procedure_sources (chunk_id);

-- procedure_versions (audit / rollback) -------------------------------------
CREATE TABLE IF NOT EXISTS procedure_versions (
    id                SERIAL PRIMARY KEY,
    procedure_id      INTEGER REFERENCES procedures(id) ON DELETE CASCADE,
    company_id        TEXT NOT NULL,
    version           INTEGER NOT NULL,
    snapshot          JSONB NOT NULL,
    cluster_signature TEXT,
    created_at        TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_proc_version UNIQUE (procedure_id, version)
);
CREATE INDEX IF NOT EXISTS ix_pversion_company ON procedure_versions (company_id);

-- knowledge_conflicts --------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_conflicts (
    id              SERIAL PRIMARY KEY,
    company_id      TEXT NOT NULL,
    user_id         INTEGER NOT NULL,
    procedure_id    INTEGER REFERENCES procedures(id) ON DELETE SET NULL,
    description     TEXT NOT NULL,
    conflict_type   conflict_type NOT NULL DEFAULT 'ambiguous',
    chunk_id_a      INTEGER,
    chunk_id_b      INTEGER,
    status          conflict_status NOT NULL DEFAULT 'open',
    resolution_note TEXT,
    detected_at     TIMESTAMPTZ DEFAULT now(),
    resolved_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_conflict_company ON knowledge_conflicts (company_id);
CREATE INDEX IF NOT EXISTS ix_conflict_status  ON knowledge_conflicts (status);

-- synthesis_runs (observability) --------------------------------------------
CREATE TABLE IF NOT EXISTS synthesis_runs (
    id                    SERIAL PRIMARY KEY,
    company_id            TEXT NOT NULL,
    user_id               INTEGER NOT NULL,
    status                run_status NOT NULL DEFAULT 'running',
    chunks_considered     INTEGER DEFAULT 0,
    clusters_found        INTEGER DEFAULT 0,
    procedures_created    INTEGER DEFAULT 0,
    procedures_updated    INTEGER DEFAULT 0,
    procedures_unchanged  INTEGER DEFAULT 0,
    conflicts_found       INTEGER DEFAULT 0,
    llm_calls             INTEGER DEFAULT 0,
    error                 TEXT,
    started_at            TIMESTAMPTZ DEFAULT now(),
    finished_at           TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_run_company ON synthesis_runs (company_id);

-- ============================================================
-- KRAB — Drop Ticket System Tables Migration
-- Run this on BOTH local Docker DB and Azure DB
-- ============================================================

-- 1. Remove FK from help_articles that referenced tickets
ALTER TABLE help_articles DROP COLUMN IF EXISTS source_ticket_id;
ALTER TABLE help_articles DROP COLUMN IF EXISTS tickets_deflected;

-- 2. Remove FK from widget_tickets that referenced tickets
ALTER TABLE widget_tickets DROP COLUMN IF EXISTS ticket_id;

-- 3. Remove FK from users (assigned_tickets relationship is ORM-only, no column to drop)

-- 4. Drop ticket-related tables (order matters due to FKs)
DROP TABLE IF EXISTS ticket_events CASCADE;
DROP TABLE IF EXISTS ticket_comments CASCADE;
DROP TABLE IF EXISTS tickets CASCADE;
DROP TABLE IF EXISTS ticket_counters CASCADE;
DROP TABLE IF EXISTS sla_policies CASCADE;
DROP TABLE IF EXISTS triggers CASCADE;
DROP TABLE IF EXISTS macros CASCADE;

-- 5. Drop widget_tickets table (was linked to ticket system)
DROP TABLE IF EXISTS widget_tickets CASCADE;

-- 6. Drop related indexes (CASCADE above handles most, but just in case)
DROP INDEX IF EXISTS ix_tickets_status_priority;
DROP INDEX IF EXISTS ix_ticket_comments_ticket;
DROP INDEX IF EXISTS ix_ticket_events_ticket;
DROP INDEX IF EXISTS idx_widget_tickets_user;
DROP INDEX IF EXISTS idx_widget_tickets_status;

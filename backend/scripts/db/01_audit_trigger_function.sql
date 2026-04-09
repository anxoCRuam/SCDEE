-- ============================================================
-- Audit log immutability trigger
-- ============================================================
-- This function + trigger prevents any UPDATE or DELETE on the
-- audit log table at the PostgreSQL level. It runs BEFORE the
-- operation, so the row is never actually modified.
--
-- This is defense-in-depth: the Django model also prevents
-- modifications at the application level, but this trigger
-- catches any bypass (raw SQL, pg admin, migration accidents).
--
-- The trigger is created on the table name that Django generates:
--   audit_auditlog (app_label + model_name in lowercase)
--
-- IMPORTANT: This script runs on first DB container start.
-- The trigger must be applied AFTER Django migrations create
-- the table. A separate management command or migration handles
-- the actual trigger creation. This file serves as documentation
-- and a template.
--
-- Reference: RF-16.1
-- ============================================================

-- Create the prevention function (idempotent).
CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Modifications to the audit log table are forbidden. '
                    'Audit records are immutable by design.';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- The trigger itself is created by Django migration 0002 in the
-- audit app, because the table doesn't exist until migrations run.
-- See: apps/audit/migrations/0002_audit_immutability_trigger.py

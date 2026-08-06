CREATE TABLE mnemo_team.workspace_checkpoint_quotas (
    workspace_id uuid PRIMARY KEY
        REFERENCES mnemo_team.workspaces(workspace_id) ON DELETE CASCADE,
    max_aggregate_count bigint NOT NULL CHECK (max_aggregate_count >= 1),
    max_revision_count bigint NOT NULL CHECK (max_revision_count >= 1),
    max_payload_bytes bigint NOT NULL CHECK (max_payload_bytes >= 1),
    updated_at timestamptz NOT NULL
);

COMMENT ON TABLE mnemo_team.workspace_checkpoint_quotas IS
    'Administrator-provisioned hard limits for canonical checkpoint storage';
COMMENT ON COLUMN mnemo_team.workspace_checkpoint_quotas.max_payload_bytes IS
    'UTF-8 bytes in canonical jsonb text for retained revision content and evidence';

CREATE FUNCTION mnemo_team.enforce_checkpoint_aggregate_quota()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    aggregate_limit bigint;
    aggregate_count bigint;
BEGIN
    IF NEW.workspace_id IS DISTINCT FROM mnemo_team.current_workspace() THEN
        RAISE EXCEPTION 'checkpoint quota scope mismatch' USING ERRCODE = '42501';
    END IF;

    SELECT quota.max_aggregate_count
      INTO aggregate_limit
      FROM mnemo_team.workspace_checkpoint_quotas AS quota
     WHERE quota.workspace_id = NEW.workspace_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'checkpoint quota is not provisioned' USING ERRCODE = 'MZQ01';
    END IF;

    SELECT count(*)
      INTO aggregate_count
      FROM mnemo_team.checkpoint_aggregates AS aggregate
     WHERE aggregate.workspace_id = NEW.workspace_id;
    IF aggregate_count >= aggregate_limit THEN
        RAISE EXCEPTION 'checkpoint aggregate quota exceeded' USING ERRCODE = 'MZQ01';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER checkpoint_aggregate_quota_guard
BEFORE INSERT ON mnemo_team.checkpoint_aggregates
FOR EACH ROW EXECUTE FUNCTION mnemo_team.enforce_checkpoint_aggregate_quota();

CREATE FUNCTION mnemo_team.enforce_checkpoint_revision_quota()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    revision_limit bigint;
    payload_limit bigint;
    revision_count bigint;
    retained_payload_bytes bigint;
    proposed_payload_bytes bigint;
BEGIN
    IF NEW.workspace_id IS DISTINCT FROM mnemo_team.current_workspace() THEN
        RAISE EXCEPTION 'checkpoint quota scope mismatch' USING ERRCODE = '42501';
    END IF;

    SELECT quota.max_revision_count, quota.max_payload_bytes
      INTO revision_limit, payload_limit
      FROM mnemo_team.workspace_checkpoint_quotas AS quota
     WHERE quota.workspace_id = NEW.workspace_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'checkpoint quota is not provisioned' USING ERRCODE = 'MZQ01';
    END IF;

    SELECT count(*),
           coalesce(sum(
               octet_length(revision.content_json::text)
               + octet_length(revision.evidence_json::text)
           ), 0)
      INTO revision_count, retained_payload_bytes
      FROM mnemo_team.checkpoint_revisions AS revision
     WHERE revision.workspace_id = NEW.workspace_id;
    proposed_payload_bytes := octet_length(NEW.content_json::text)
        + octet_length(NEW.evidence_json::text);
    IF revision_count >= revision_limit
       OR retained_payload_bytes + proposed_payload_bytes > payload_limit
    THEN
        RAISE EXCEPTION 'checkpoint revision quota exceeded' USING ERRCODE = 'MZQ01';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER checkpoint_revision_quota_guard
BEFORE INSERT ON mnemo_team.checkpoint_revisions
FOR EACH ROW EXECUTE FUNCTION mnemo_team.enforce_checkpoint_revision_quota();

REVOKE ALL ON mnemo_team.workspace_checkpoint_quotas FROM PUBLIC;
REVOKE ALL ON FUNCTION mnemo_team.enforce_checkpoint_aggregate_quota() FROM PUBLIC;
REVOKE ALL ON FUNCTION mnemo_team.enforce_checkpoint_revision_quota() FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (22, CURRENT_TIMESTAMP);

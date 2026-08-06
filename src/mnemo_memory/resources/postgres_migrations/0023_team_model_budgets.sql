CREATE TABLE mnemo_team.workspace_model_budgets (
    workspace_id uuid NOT NULL
        REFERENCES mnemo_team.workspaces(workspace_id) ON DELETE CASCADE,
    task_type text NOT NULL CHECK (task_type IN ('episodic_candidate_extraction')),
    max_call_count bigint NOT NULL CHECK (max_call_count >= 1),
    max_input_tokens bigint NOT NULL CHECK (max_input_tokens >= 1),
    max_output_tokens bigint NOT NULL CHECK (max_output_tokens >= 1),
    max_cost_microusd bigint NOT NULL CHECK (max_cost_microusd >= 0),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, task_type)
);

CREATE TABLE mnemo_team.workspace_model_budget_usage (
    workspace_id uuid NOT NULL,
    usage_date date NOT NULL,
    task_type text NOT NULL CHECK (task_type IN ('episodic_candidate_extraction')),
    used_call_count bigint NOT NULL CHECK (used_call_count >= 0),
    used_input_tokens bigint NOT NULL CHECK (used_input_tokens >= 0),
    used_output_tokens bigint NOT NULL CHECK (used_output_tokens >= 0),
    used_cost_microusd bigint NOT NULL CHECK (used_cost_microusd >= 0),
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (workspace_id, usage_date, task_type),
    FOREIGN KEY (workspace_id, task_type)
        REFERENCES mnemo_team.workspace_model_budgets(workspace_id, task_type)
        ON DELETE CASCADE
);

COMMENT ON TABLE mnemo_team.workspace_model_budgets IS
    'Administrator-provisioned daily worst-case model-call limits by workspace and task';
COMMENT ON TABLE mnemo_team.workspace_model_budget_usage IS
    'UTC-day model-call reservations charged before provider invocation';

CREATE FUNCTION mnemo_team.reserve_model_budget(
    target_workspace uuid,
    target_task_type text,
    requested_input_tokens bigint,
    requested_output_tokens bigint,
    requested_cost_microusd bigint
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    member_role text;
    daily_limit mnemo_team.workspace_model_budgets%ROWTYPE;
    utc_date date := (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date;
BEGIN
    IF target_workspace IS DISTINCT FROM mnemo_team.current_workspace()
       OR mnemo_team.current_operation() IS DISTINCT FROM 'contribute'
       OR requested_input_tokens < 1
       OR requested_output_tokens < 1
       OR requested_cost_microusd < 0
    THEN
        RAISE EXCEPTION 'model budget authorization failed' USING ERRCODE = '42501';
    END IF;

    SELECT membership.role
      INTO member_role
      FROM mnemo_team.workspace_memberships AS membership
     WHERE membership.workspace_id = target_workspace
       AND membership.principal_id = mnemo_team.current_principal()
       AND membership.status = 'active';
    IF NOT FOUND OR NOT mnemo_team.workspace_role_allowed(member_role, 'contribute') THEN
        RAISE EXCEPTION 'model budget authorization failed' USING ERRCODE = '42501';
    END IF;

    SELECT budget.*
      INTO daily_limit
      FROM mnemo_team.workspace_model_budgets AS budget
     WHERE budget.workspace_id = target_workspace
       AND budget.task_type = target_task_type
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'model budget is not provisioned' USING ERRCODE = 'MZB01';
    END IF;

    INSERT INTO mnemo_team.workspace_model_budget_usage(
        workspace_id,
        usage_date,
        task_type,
        used_call_count,
        used_input_tokens,
        used_output_tokens,
        used_cost_microusd,
        updated_at
    ) VALUES (
        target_workspace,
        utc_date,
        target_task_type,
        0,
        0,
        0,
        0,
        CURRENT_TIMESTAMP
    ) ON CONFLICT (workspace_id, usage_date, task_type) DO NOTHING;

    UPDATE mnemo_team.workspace_model_budget_usage AS usage
       SET used_call_count = (usage.used_call_count::numeric + 1)::bigint,
           used_input_tokens = (
               usage.used_input_tokens::numeric + requested_input_tokens
           )::bigint,
           used_output_tokens = (
               usage.used_output_tokens::numeric + requested_output_tokens
           )::bigint,
           used_cost_microusd = (
               usage.used_cost_microusd::numeric + requested_cost_microusd
           )::bigint,
           updated_at = CURRENT_TIMESTAMP
     WHERE usage.workspace_id = target_workspace
       AND usage.usage_date = utc_date
       AND usage.task_type = target_task_type
       AND usage.used_call_count::numeric + 1 <= daily_limit.max_call_count
       AND usage.used_input_tokens::numeric + requested_input_tokens
           <= daily_limit.max_input_tokens
       AND usage.used_output_tokens::numeric + requested_output_tokens
           <= daily_limit.max_output_tokens
       AND usage.used_cost_microusd::numeric + requested_cost_microusd
           <= daily_limit.max_cost_microusd;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'model budget is exhausted' USING ERRCODE = 'MZB01';
    END IF;
END;
$$;

REVOKE ALL ON mnemo_team.workspace_model_budgets FROM PUBLIC;
REVOKE ALL ON mnemo_team.workspace_model_budget_usage FROM PUBLIC;
REVOKE ALL ON FUNCTION mnemo_team.reserve_model_budget(uuid, text, bigint, bigint, bigint)
    FROM PUBLIC;

INSERT INTO mnemo_team.schema_migrations(version, applied_at)
VALUES (23, CURRENT_TIMESTAMP);

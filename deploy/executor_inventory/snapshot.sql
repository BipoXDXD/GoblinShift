-- Executed only inside the adapter-owned read-only transaction.
WITH
r AS MATERIALIZED (
    SELECT id, executor_digest, status, phase,
           executor_digest IS NOT NULL AND
             executor_digest ~ '^[a-z0-9][a-z0-9._/:-]{0,254}@sha256:[0-9a-f]{64}$'
             AS valid_reference,
           status IN ('queued', 'running', 'stopping', 'succeeded',
                      'succeeded_with_warnings', 'canceled', 'failed')
             AND ((status = 'running' AND phase IN
               ('inspecting', 'processing', 'packaging', 'finalizing', 'retry_wait'))
               OR (status <> 'running' AND phase IS NULL)) AS valid_state
    FROM __SCHEMA__."run"
),
a AS MATERIALIZED (
    SELECT run_id, ended_at IS NULL AS unresolved,
           executor_started_at IS NOT NULL AS physical
    FROM __SCHEMA__."attempt"
),
ac AS MATERIALIZED (
    SELECT run_id, count(*) AS attempts,
           count(*) FILTER (WHERE unresolved) AS unresolved,
           count(*) FILTER (WHERE NOT unresolved) AS ended,
           count(*) FILTER (WHERE physical) AS physical,
           count(*) FILTER (WHERE NOT physical) AS never_started,
           count(*) FILTER (WHERE unresolved AND physical) AS unresolved_physical
    FROM a GROUP BY run_id
),
rg AS MATERIALIZED (
    SELECT r.executor_digest AS reference,
           CASE WHEN r.valid_state THEN r.status ELSE 'INVALID' END AS status,
           CASE WHEN r.valid_state THEN r.phase ELSE NULL END AS phase,
           count(*) AS runs,
           count(*) FILTER (WHERE ac.run_id IS NULL) AS runs_without_attempt,
           coalesce(sum(ac.attempts), 0) AS attempts,
           coalesce(sum(ac.unresolved), 0) AS unresolved,
           coalesce(sum(ac.ended), 0) AS ended,
           coalesce(sum(ac.physical), 0) AS physical,
           coalesce(sum(ac.never_started), 0) AS never_started,
           coalesce(sum(ac.unresolved_physical), 0) AS unresolved_physical
    FROM r LEFT JOIN ac ON ac.run_id = r.id
    WHERE r.valid_reference
    GROUP BY r.executor_digest,
             CASE WHEN r.valid_state THEN r.status ELSE 'INVALID' END,
             CASE WHEN r.valid_state THEN r.phase ELSE NULL END
),
sg AS MATERIALIZED (
    SELECT CASE WHEN valid_state THEN status ELSE 'INVALID' END AS status,
           CASE WHEN valid_state THEN phase ELSE NULL END AS phase,
           count(*) AS runs
    FROM r
    GROUP BY CASE WHEN valid_state THEN status ELSE 'INVALID' END,
             CASE WHEN valid_state THEN phase ELSE NULL END
),
t AS MATERIALIZED (
    SELECT
      (SELECT count(*) FROM r) AS runs,
      (SELECT count(*) FROM a) AS attempts,
      (SELECT count(*) FROM a WHERE unresolved) AS unresolved,
      (SELECT count(*) FROM a WHERE NOT unresolved) AS ended,
      (SELECT count(*) FROM a WHERE physical) AS physical,
      (SELECT count(*) FROM a WHERE NOT physical) AS never_started,
      (SELECT count(*) FROM a WHERE unresolved AND physical) AS unresolved_physical,
      (SELECT count(*) FROM r LEFT JOIN ac ON r.id = ac.run_id
        WHERE ac.run_id IS NULL) AS runs_without_attempt,
      (SELECT count(*) FROM r WHERE NOT coalesce(valid_reference, false)) AS invalid_refs,
      (SELECT count(*) FROM r WHERE NOT coalesce(valid_state, false)) AS invalid_states,
      (SELECT count(*) FROM a LEFT JOIN r ON a.run_id = r.id
        WHERE r.id IS NULL) AS orphan_attempts,
      (SELECT count(*) FROM ac WHERE unresolved > 1) AS multiple_unresolved,
      (SELECT count(*) FROM rg) AS reference_state_groups,
      (SELECT count(DISTINCT executor_digest) FROM r WHERE valid_reference) AS references,
      (SELECT coalesce(sum(runs), 0) FROM rg) AS grouped_runs,
      (SELECT coalesce(sum(attempts), 0) FROM rg) AS grouped_attempts,
      (SELECT coalesce(sum(unresolved), 0) FROM rg) AS grouped_unresolved,
      (SELECT coalesce(sum(ended), 0) FROM rg) AS grouped_ended,
      (SELECT coalesce(sum(physical), 0) FROM rg) AS grouped_physical,
      (SELECT coalesce(sum(never_started), 0) FROM rg) AS grouped_never_started,
      (SELECT coalesce(sum(unresolved_physical), 0) FROM rg) AS grouped_unresolved_physical,
      (SELECT coalesce(sum(runs_without_attempt), 0) FROM rg) AS grouped_without_attempt
),
checks AS (
    SELECT *, invalid_refs = 0 AND invalid_states = 0 AND orphan_attempts = 0
      AND multiple_unresolved = 0 AND reference_state_groups <= 4096
      AND runs = grouped_runs AND attempts = grouped_attempts
      AND unresolved = grouped_unresolved AND ended = grouped_ended
      AND physical = grouped_physical AND never_started = grouped_never_started
      AND unresolved_physical = grouped_unresolved_physical
      AND runs_without_attempt = grouped_without_attempt AS reconciled
    FROM t
)
SELECT jsonb_build_object(
    'format', 'goblin-retained-db-slice-1',
    'status', CASE WHEN reconciled THEN 'DB_SLICE_ONLY_UNVERIFIED'
                   ELSE 'BLOCKED_NO_PARTIAL_EXPORT' END,
    'ready_for_seed', false,
    'coverage', 'all retained run and attempt rows visible to reviewed role/relations',
    'transaction_isolation', current_setting('transaction_isolation'),
    'transaction_read_only', current_setting('transaction_read_only'),
    'snapshot', txid_current_snapshot()::text,
    'statement_at', statement_timestamp(),
    'counts', to_jsonb(checks),
    'run_states', (SELECT coalesce(jsonb_agg(to_jsonb(sg) ORDER BY status, phase),
                                          '[]'::jsonb) FROM sg),
    -- Never emit a truncated reference list or any malformed raw reference/state.
    'reference_states', CASE WHEN reconciled THEN
       (SELECT coalesce(jsonb_agg(to_jsonb(rg) ORDER BY reference, status, phase),
                        '[]'::jsonb) FROM rg) ELSE NULL END,
    'missing_evidence', jsonb_build_array(
       'operator environment/relation/role identity and authentic complete receipt',
       'coordinated ingress/cut binding and freshness through transition',
       'approved provenance per full reference, including all retained terminal refs',
       'retention boundary and approved current/candidate/rollback references outside DB',
       'actual active reader opening approved policy with instance/fd/bytes binding',
       'installed receiver/senders/config/policy/return compatibility'
    )
)::text, run_origin.id, attempt_origin.run_id
FROM checks
-- NULL-only projections retain server-provided table origins through the same CTEs.
-- No Run/Attempt identity value is returned, even when the tables are empty.
LEFT JOIN r AS run_origin ON false
LEFT JOIN a AS attempt_origin ON false;

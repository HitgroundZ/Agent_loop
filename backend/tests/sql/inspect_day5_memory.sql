\echo '=== Alembic revision ==='
SELECT version_num FROM alembic_version;

\echo '=== long_term_memories ==='
\d+ long_term_memories

\echo '=== conversation_messages ==='
\d+ conversation_messages

\echo '=== tool_actions ==='
\d+ tool_actions

\echo '=== tool_outbox ==='
\d+ tool_outbox

\echo '=== agent_runs Day5 columns ==='
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'agent_runs'
  AND column_name IN ('user_id', 'memory_context', 'retrieval_mode', 'routing_decision', 'continuation_context')
ORDER BY ordinal_position;

\echo '=== Day5 demo memory rows ==='
SELECT id,
       user_id,
       category,
       enabled,
       source_message_id,
       source_document_id,
       parent_memory_id,
       access_count,
       left(content, 100) AS content_preview,
       created_at
FROM long_term_memories
WHERE user_id = 'day5-structure-demo'
ORDER BY created_at, category;

\echo '=== Trace source messages ==='
SELECT m.id AS memory_id,
       m.category,
       m.enabled,
       cm.id AS source_message_id,
       cm.session_id,
       cm.role,
       left(cm.content, 100) AS source_content
FROM long_term_memories AS m
LEFT JOIN conversation_messages AS cm ON cm.id = m.source_message_id
WHERE m.user_id = 'day5-structure-demo'
ORDER BY m.created_at, m.category;

\echo '=== Recent tool actions ==='
SELECT id,
       run_id,
       tool_name,
       risk_level,
       permission,
       side_effect,
       status,
       attempt_count,
       left(arguments_summary, 100) AS arguments_summary,
       created_at
FROM tool_actions
ORDER BY created_at DESC
LIMIT 20;

\echo '=== Outbox enqueue results ==='
SELECT id, action_id, channel, recipient, status, created_at, delivered_at
FROM tool_outbox
ORDER BY created_at DESC
LIMIT 20;

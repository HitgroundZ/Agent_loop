\echo '=== Alembic revision ==='
SELECT version_num FROM alembic_version;

\echo '=== long_term_memories ==='
\d+ long_term_memories

\echo '=== conversation_messages ==='
\d+ conversation_messages

\echo '=== agent_runs Day5 columns ==='
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'agent_runs'
  AND column_name IN ('user_id', 'memory_context')
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

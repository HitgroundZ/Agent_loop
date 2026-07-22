# Agent Loop Day 10 架构与数据模型

## 1. 系统架构图

```mermaid
flowchart LR
    U["浏览器 / 用户"] -->|HTTP| FE["Vue 3 Production Build"]
    FE -->|/api 反向代理| API["FastAPI Backend"]

    subgraph Core["Agent 与知识库核心"]
        API --> AL["Agent Loop 状态机"]
        AL --> TR["Tool Registry / Risk Policy"]
        AL --> RET["Hybrid Retrieval + Rerank"]
        AL --> MEM["短期 / 长期记忆"]
        AL --> EVAL["引用与终态评估"]
    end

    API --> PG[("PostgreSQL + pgvector")]
    API --> R[("Redis")]
    API --> M[("MinIO")]
    API -->|embedding job| R
    W["Embedding Worker"] -->|consume| R
    W -->|vector write| PG
    W -->|DashScope embedding| LLM["Qwen / DashScope"]
    RET -->|embedding + rerank| LLM
    AL -->|Function Calling| LLM

    TR -->|safe argv only| SB["Sandbox Service"]
    SB -->|Docker SDK| D["Docker Daemon"]
    D --> C["一次性无网络容器"]

    AL -->|Trace / ToolAction / Eval| PG
    TR -->|high risk| APPROVAL["人工审批台"]
    APPROVAL -->|approve / reject| AL
```

关键边界：Backend 不持有 Docker socket；只有 Sandbox Service 能创建一次性容器。角色和权限由服务端配置决定，前端请求体不能自行声明角色。知识库、记忆和工具输出均视为不可信数据，不能扩大原始用户意图授权。

## 2. 数据库关系图

```mermaid
erDiagram
    DOCUMENTS ||--o{ DOCUMENT_VERSIONS : has
    DOCUMENTS ||--o{ DOCUMENT_CHUNKS : owns
    DOCUMENTS ||--o{ EMBEDDING_JOBS : queues
    DOCUMENT_VERSIONS ||--o{ DOCUMENT_CHUNKS : splits
    DOCUMENT_VERSIONS ||--o{ EMBEDDING_JOBS : embeds

    AGENT_RUNS ||--o{ AGENT_TRACE_EVENTS : traces
    AGENT_RUNS ||--o{ CONVERSATION_MESSAGES : records
    AGENT_RUNS ||--o{ TOOL_ACTIONS : proposes
    TOOL_ACTIONS ||--o| TOOL_OUTBOX : emits

    DOCUMENTS {
        uuid id PK
        string source_hash UK
        string status
        string tenant_id
        string workspace_id
        jsonb permissions
    }
    DOCUMENT_VERSIONS {
        uuid id PK
        uuid document_id FK
        int version_no
        string source_object_key
        string extracted_text_object_key
    }
    DOCUMENT_CHUNKS {
        uuid id PK
        uuid document_id FK
        uuid version_id FK
        int chunk_index
        text text
        vector embedding
        jsonb permissions
    }
    EMBEDDING_JOBS {
        uuid id PK
        uuid document_id FK
        uuid version_id FK
        string idempotency_key UK
        string status
        int attempts
        int max_attempts
    }
    AGENT_RUNS {
        uuid id PK
        string user_id
        string session_id
        string status
        jsonb evaluation
        jsonb citations
    }
    AGENT_TRACE_EVENTS {
        uuid id PK
        uuid run_id FK
        int sequence UK
        string state
        jsonb input_payload
        jsonb output_payload
    }
    CONVERSATION_MESSAGES {
        uuid id PK
        uuid run_id FK
        string user_id
        string session_id
        string role
    }
    TOOL_ACTIONS {
        uuid id PK
        uuid run_id FK
        string tool_call_id UK
        string risk_level
        string status
        string approved_by
    }
    TOOL_OUTBOX {
        uuid id PK
        uuid action_id FK
        string status
        string destination
    }
    LONG_TERM_MEMORIES {
        uuid id PK
        string user_id
        string category
        text content
        string source_message_id
        boolean enabled
    }
    IDEMPOTENCY_RECORDS {
        string key PK
        string scope
        int status_code
        json response_json
    }
```

`LONG_TERM_MEMORIES` 的来源 ID 刻意不设外键：原始文档被删除后，记忆仍需保留可审计的稳定来源标识。`IDEMPOTENCY_RECORDS` 同时保护上传、删除和审批 API；`TOOL_ACTIONS(run_id, tool_call_id)` 约束避免同一模型调用重复落库。

## 3. Agent 状态机图

```mermaid
stateDiagram-v2
    [*] --> created
    created --> analyzing
    analyzing --> acting
    analyzing --> evaluating: 模型直接回答
    acting --> retrieving: 调用知识库工具
    retrieving --> acting: 返回带 citation_id 的结果
    acting --> waiting_approval: 中高风险动作需审批
    waiting_approval --> acting: 批准或拒绝后续跑
    acting --> evaluating: 工具完成
    evaluating --> completed: 回答与引用校验通过
    evaluating --> escalated_to_human: 检索失败或缺少真实引用
    created --> failed: 未处理异常
    analyzing --> failed: 未处理异常
    acting --> failed: 未处理异常
    waiting_approval --> failed: 续跑异常
    completed --> [*]
    escalated_to_human --> [*]
    failed --> [*]
```

每次迁移都会生成递增 `sequence` 的 `AgentTraceEvent`。终态不是只看模型文本：如果知识库路径已执行但最终答案未引用本轮真实 `C<n>`，系统会把运行标记为 `escalated_to_human`。

## 4. 完整请求时序

```mermaid
sequenceDiagram
    actor User as 用户
    participant UI as 前端
    participant API as Backend
    participant KB as Retrieval
    participant LLM as Model
    participant Tool as Tool Executor
    participant DB as PostgreSQL

    User->>UI: 提问
    UI->>API: POST /api/agent/runs
    API->>DB: 创建 run + user message + trace
    API->>LLM: system prompt + 可见工具
    LLM-->>API: tool call: search_knowledge_base
    API->>KB: tenant/workspace/subject 过滤 + 检索
    KB-->>API: C1..Cn
    API->>LLM: 不可信工具结果 + citation_id
    LLM-->>API: 带 [C1] 的回答 / 高风险 tool call
    alt 高风险动作
        API->>Tool: 记录 pending ToolAction
        API-->>UI: waiting_approval
        User->>UI: 审批
        UI->>API: Idempotency-Key + decision
        API->>Tool: 单次执行或拒绝
        API->>LLM: 续跑
    end
    API->>API: citation 与 handoff eval
    API->>DB: 保存 answer + trace + evaluation
    API-->>UI: 最终回答、引用、工具和 trace
```

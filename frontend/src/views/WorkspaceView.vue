<script setup>
import MemoryManager from '../components/MemoryManager.vue'
import { useKnowledgeWorkspace } from '../composables/useKnowledgeWorkspace'


const {
  documents, selectedDocument, selectedChunks, selectedFile, loading, chunksLoading,
  uploading, deleting, retrying, errorMessage, health, uploadTenantId, uploadWorkspaceId,
  uploadTags, uploadPermissions, retrievalQuery, retrievalStrategy, retrievalTopK,
  retrievalTenantId, retrievalWorkspaceId, retrievalTags, retrievalPrincipal,
  retrievalUseSelectedDocument, retrievalLoading, retrievalError, agentQuestion, agentUserId,
  agentSessionId, agentLoading, agentError, agentResult, agentTraceExpanded, indexedCount,
  processingCount, failedCount, selectedEmbeddingSummary, selectedHasFailedEmbedding,
  displayedRetrievalGroups, agentTracePreview, agentCitations, agentMemoryContext,
  agentSessionMessages, agentShortTermState, pickFile, uploadDocument, openDocument,
  retryEmbedding, deleteSelected, runRetrieval, compareRetrieval, runAgent, formatScore,
  formatDuration, formatTokens, agentStateClass, agentStateLabel, memoryCategoryLabel,
  roleLabel, statusLabel, formatBytes, formatDate
} = useKnowledgeWorkspace()
</script>

<template>
  <main class="layout">
    <aside class="sidebar">
      <div class="brand">
        <div>
          <p class="eyebrow">Day 5</p>
          <h1>Agent Loop</h1>
        </div>
        <span class="health" :class="health">{{ health }}</span>
      </div>

      <section class="panel upload-panel">
        <div class="upload-row">
          <label class="file-picker" for="document-file">
            <input id="document-file" type="file" accept=".pdf,.docx,.md,.markdown,.html,.htm" @change="pickFile" />
            <span>{{ selectedFile ? selectedFile.name : '选择文档' }}</span>
          </label>
          <button class="primary" :disabled="uploading || !selectedFile" @click="uploadDocument">
            {{ uploading ? '上传中' : '上传' }}
          </button>
        </div>
        <div class="form-grid upload-metadata">
          <label><span>租户</span><input v-model="uploadTenantId" type="text" placeholder="default" /></label>
          <label><span>工作区</span><input v-model="uploadWorkspaceId" type="text" placeholder="default" /></label>
          <label class="wide-field"><span>标签</span><input v-model="uploadTags" type="text" placeholder="标签1, 标签2" /></label>
          <label class="wide-field">
            <span>权限 JSON</span>
            <textarea v-model="uploadPermissions" rows="2" placeholder='{"subjects":["team-a"]}'></textarea>
          </label>
        </div>
      </section>

      <section class="stats">
        <div><strong>{{ documents.length }}</strong><span>文档</span></div>
        <div><strong>{{ indexedCount }}</strong><span>入库</span></div>
        <div><strong>{{ processingCount }}</strong><span>处理中</span></div>
        <div><strong>{{ failedCount }}</strong><span>失败</span></div>
      </section>
      <p v-if="errorMessage" class="error">{{ errorMessage }}</p>

      <section class="document-list" aria-label="文档列表">
        <button
          v-for="item in documents"
          :key="item.id"
          class="document-row"
          :class="{ active: selectedDocument?.id === item.id }"
          @click="openDocument(item.id)"
        >
          <span class="filename">{{ item.filename }}</span>
          <span class="row-meta">
            <span class="status" :class="item.status">{{ statusLabel(item.status) }}</span>
            <span>{{ item.chunk_count || 0 }} chunks</span>
            <span>{{ formatBytes(item.size_bytes) }}</span>
          </span>
        </button>
        <div v-if="!loading && documents.length === 0" class="empty">暂无文档</div>
      </section>
    </aside>

    <section class="content">
      <section class="panel agent-panel">
        <header class="agent-header">
          <div>
            <p class="eyebrow">第 5 天 · 选择性记忆注入</p>
            <h2>智能体运行</h2>
          </div>
          <div class="agent-actions identity-actions">
            <input v-model="agentUserId" type="text" placeholder="用户 ID" aria-label="用户 ID" />
            <input v-model="agentSessionId" type="text" placeholder="会话 ID（留空新建）" aria-label="会话 ID" />
            <button class="primary" :disabled="agentLoading" @click="runAgent">
              {{ agentLoading ? '运行中' : '向智能体提问' }}
            </button>
          </div>
        </header>
        <div class="agent-input-row">
          <input v-model="agentQuestion" type="text" placeholder="输入问题；记忆按用户 ID 隔离并跨会话召回。" @keyup.enter="runAgent" />
        </div>
        <p v-if="agentError" class="error">{{ agentError }}</p>

        <div v-if="agentResult" class="agent-output">
          <div class="agent-status-bar">
            <span class="status" :class="agentStateClass(agentResult.current_state)">
              {{ agentStateLabel(agentResult.current_state) }}
            </span>
            <span>用户 {{ agentResult.user_id }}</span>
            <span>运行 {{ agentResult.id }}</span>
            <span>会话 {{ agentResult.session_id }}</span>
            <span>{{ formatTokens(agentResult.token_usage) }}</span>
          </div>

          <div class="state-flow">
            <span
              v-for="(state, index) in agentResult.state_flow"
              :key="`${state}-${index}`"
              class="state-chip"
              :class="agentStateClass(state)"
            >
              {{ index + 1 }}. {{ agentStateLabel(state) }}
            </span>
          </div>

          <section class="short-term-grid" aria-label="Redis 短期记忆状态">
            <div>
              <span>检索缓存</span>
              <strong>{{ agentShortTermState.retrieval_cache?.hit ? '命中' : '未命中' }}</strong>
            </div>
            <div>
              <span>Pending approval</span>
              <strong>{{ agentShortTermState.pending_approval?.status || '无' }}</strong>
            </div>
            <div>
              <span>Rate limit</span>
              <strong>{{ agentShortTermState.rate_limit?.remaining ?? '-' }} / {{ agentShortTermState.rate_limit?.limit ?? '-' }}</strong>
            </div>
            <div>
              <span>Token budget</span>
              <strong>{{ agentShortTermState.token_budget?.remaining ?? '-' }} / {{ agentShortTermState.token_budget?.limit ?? '-' }}</strong>
            </div>
          </section>

          <section class="agent-answer">
            <h3>回答</h3>
            <pre>{{ agentResult.answer || '暂无回答' }}</pre>
          </section>

          <section class="agent-citations">
            <h3>可追溯引用</h3>
            <div v-if="agentCitations.length === 0" class="empty">暂无引用</div>
            <article v-for="item in agentCitations" :key="item.id" class="citation-row">
              <header>
                <strong>{{ item.label }} {{ item.document_name }}</strong>
                <template v-if="item.retrieval_source === 'memory'">
                  <span>{{ memoryCategoryLabel(item.memory_category) }}</span>
                  <span v-if="item.source_message_id">来源消息 {{ item.source_message_id }}</span>
                  <span v-if="item.source_document_id">来源文档 {{ item.source_document_id }}</span>
                </template>
                <template v-else>
                  <span>切片 {{ (item.chunk_index ?? 0) + 1 }}</span>
                  <span v-if="item.page">第 {{ item.page }} 页</span>
                  <span v-if="item.heading">{{ item.heading }}</span>
                </template>
                <span>得分 {{ formatScore(item.score) }}</span>
              </header>
              <p>{{ item.snippet }}</p>
            </article>
          </section>

          <section class="agent-memory-context">
            <h3>本轮选择性注入（{{ agentMemoryContext.length }} 条）</h3>
            <div v-if="agentMemoryContext.length === 0" class="empty">没有向 prompt 注入长期记忆</div>
            <div v-else class="memory-injection-list">
              <article v-for="memory in agentMemoryContext" :key="memory.id">
                <strong>{{ memoryCategoryLabel(memory.category) }}</strong>
                <span>{{ memory.content }}</span>
                <small>{{ memory.id }} · source_message_id={{ memory.source_message_id || '-' }}</small>
              </article>
            </div>
          </section>

          <section class="agent-trace">
            <div class="trace-title-row">
              <h3>执行轨迹</h3>
              <button class="secondary compact-button" @click="agentTraceExpanded = !agentTraceExpanded">
                {{ agentTraceExpanded ? '收起详情' : '查看详情' }}
              </button>
            </div>
            <div class="trace-list">
              <article v-for="event in agentTracePreview" :key="event.sequence" class="trace-row">
                <span class="state-chip" :class="agentStateClass(event.state)">
                  {{ event.sequence }} {{ agentStateLabel(event.state) }}
                </span>
                <span>{{ event.output_summary }}</span>
                <span>{{ formatDuration(event.duration_ms) }}</span>
                <span>{{ formatTokens(event.token_usage) }}</span>
                <span v-if="event.retry_count">重试 {{ event.retry_count }}</span>
                <span v-if="event.error" class="trace-error">{{ event.error }}</span>
              </article>
            </div>
            <pre v-if="agentTraceExpanded">{{ JSON.stringify(agentResult.trace_events, null, 2) }}</pre>
          </section>

          <section class="agent-session">
            <h3>Redis 最近消息</h3>
            <div v-if="agentSessionMessages.length === 0" class="empty">暂无缓存消息</div>
            <div v-else class="message-list">
              <article v-for="message in agentSessionMessages" :key="message.id || `${message.run_id}-${message.role}-${message.created_at}`">
                <strong>{{ roleLabel(message.role) }}</strong><span>{{ message.content }}</span>
              </article>
            </div>
          </section>
        </div>
      </section>

      <MemoryManager :user-id="agentUserId" />

      <section class="panel retrieval-panel">
        <header class="retrieval-header">
          <div><p class="eyebrow">知识库</p><h2>文档检索</h2></div>
          <div class="retrieval-actions">
            <button class="secondary" :disabled="retrievalLoading" @click="compareRetrieval">对比</button>
            <button class="primary" :disabled="retrievalLoading" @click="runRetrieval">
              {{ retrievalLoading ? '检索中' : '检索' }}
            </button>
          </div>
        </header>
        <div class="retrieval-form">
          <label class="query-field"><span>问题</span><input v-model="retrievalQuery" type="text" placeholder="输入问题或关键词" @keyup.enter="runRetrieval" /></label>
          <label><span>检索策略</span><select v-model="retrievalStrategy"><option value="hybrid">混合检索</option><option value="vector">向量检索</option><option value="keyword">关键词检索</option></select></label>
          <label><span>返回数量</span><input v-model="retrievalTopK" type="number" min="1" max="50" placeholder="自动" /></label>
          <label><span>租户</span><input v-model="retrievalTenantId" type="text" placeholder="default" /></label>
          <label><span>工作区</span><input v-model="retrievalWorkspaceId" type="text" placeholder="default" /></label>
          <label><span>标签</span><input v-model="retrievalTags" type="text" placeholder="标签1, 标签2" /></label>
          <label><span>身份</span><input v-model="retrievalPrincipal" type="text" placeholder="用户/团队" /></label>
          <label class="checkbox-line"><input v-model="retrievalUseSelectedDocument" type="checkbox" :disabled="!selectedDocument" /><span>当前文档</span></label>
        </div>
        <p v-if="retrievalError" class="error">{{ retrievalError }}</p>

        <div v-if="displayedRetrievalGroups.length" class="retrieval-results">
          <article v-for="group in displayedRetrievalGroups" :key="group.key" class="retrieval-group">
            <header class="group-header">
              <div><h3>{{ group.title }}</h3><p class="muted">{{ group.result?.rewritten_query || '-' }} · 前 {{ group.result?.top_k || '-' }} 条</p></div>
              <span v-if="group.result?.need_human_handoff" class="status failed">需人工处理</span>
              <span v-else class="status indexed">{{ group.result?.results?.length || 0 }} 条引用</span>
            </header>
            <p v-if="group.result?.diagnostics?.error" class="error">{{ group.result.diagnostics.error }}</p>
            <div v-else-if="!group.result" class="empty">暂无结果</div>
            <div v-else-if="group.result?.need_human_handoff" class="empty">未检索到可靠来源</div>
            <template v-else>
              <article v-for="item in group.result.results" :key="`${group.key}-${item.chunk_id}`" class="citation-row">
                <header><strong>{{ item.document_name }}</strong><span>切片 {{ item.chunk_index + 1 }}</span><span v-if="item.page">第 {{ item.page }} 页</span><span v-if="item.heading">{{ item.heading }}</span><span>得分 {{ formatScore(item.score) }}</span></header>
                <p>{{ item.snippet }}</p><pre>{{ JSON.stringify(item.metadata, null, 2) }}</pre>
              </article>
            </template>
          </article>
        </div>
      </section>

      <div v-if="selectedDocument" class="detail">
        <header class="detail-header">
          <div><p class="eyebrow">{{ selectedDocument.file_ext }}</p><h2>{{ selectedDocument.filename }}</h2><p class="muted">{{ selectedDocument.parser_name || '未解析' }} · {{ formatDate(selectedDocument.created_at) }}</p></div>
          <div class="header-actions">
            <button v-if="selectedHasFailedEmbedding" class="secondary" :disabled="retrying" @click="retryEmbedding">{{ retrying ? '重试中' : '重试向量化' }}</button>
            <button class="danger" :disabled="deleting" @click="deleteSelected">{{ deleting ? '删除中' : '删除' }}</button>
          </div>
        </header>
        <div class="detail-grid">
          <section class="panel">
            <h3>状态</h3>
            <dl>
              <div><dt>文档状态</dt><dd><span class="status" :class="selectedDocument.status">{{ statusLabel(selectedDocument.status) }}</span></dd></div>
              <div><dt>切片数量</dt><dd>{{ selectedDocument.chunk_count || 0 }}</dd></div>
              <div><dt>向量状态</dt><dd class="summary-line"><span>已完成 {{ selectedEmbeddingSummary.embedded || 0 }}</span><span>处理中 {{ selectedEmbeddingSummary.embedding || 0 }}</span><span>等待 {{ selectedEmbeddingSummary.pending || 0 }}</span><span>失败 {{ selectedEmbeddingSummary.failed || 0 }}</span></dd></div>
              <div><dt>SHA-256</dt><dd class="hash">{{ selectedDocument.source_hash }}</dd></div>
              <div><dt>检索过滤</dt><dd class="summary-line"><span>{{ selectedDocument.tenant_id || 'default' }}</span><span>{{ selectedDocument.workspace_id || 'default' }}</span><span>{{ (selectedDocument.tags || []).join(', ') || '无标签' }}</span></dd></div>
            </dl>
          </section>
          <section class="panel"><h3>元数据</h3><pre>{{ JSON.stringify(selectedDocument.metadata, null, 2) }}</pre></section>
        </div>
        <section v-if="selectedDocument.error_message" class="panel error-panel"><h3>错误</h3><pre>{{ selectedDocument.error_message }}</pre></section>
        <section class="panel text-panel"><h3>文本预览</h3><pre>{{ selectedDocument.text_preview || '无文本' }}</pre></section>
        <section class="panel chunk-panel">
          <h3>切片</h3>
          <div v-if="chunksLoading" class="empty">加载 chunk 中</div>
          <div v-else-if="selectedChunks.length === 0" class="empty">暂无切片</div>
          <template v-else>
            <article v-for="chunk in selectedChunks" :key="chunk.id" class="chunk-row">
              <header class="chunk-header"><div><strong>切片 {{ chunk.chunk_index + 1 }}</strong><span v-if="chunk.page">第 {{ chunk.page }} 页</span><span v-if="chunk.heading">{{ chunk.heading }}</span></div><span class="status" :class="chunk.embedding.status">{{ statusLabel(chunk.embedding.status) }}</span></header>
              <pre class="chunk-text">{{ chunk.text }}</pre>
              <div class="chunk-meta"><span>{{ chunk.embedding.model || '未生成模型' }}</span><span>{{ chunk.embedding.dim || '-' }} 维</span><span>{{ chunk.embedding.has_vector ? '向量已生成' : '暂无向量' }}</span></div>
              <pre class="chunk-json">{{ JSON.stringify(chunk.metadata, null, 2) }}</pre>
            </article>
          </template>
        </section>
      </div>
      <div v-else class="placeholder"><h2>知识库文档</h2><p>等待上传</p></div>
    </section>
  </main>
</template>

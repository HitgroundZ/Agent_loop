<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'

const documents = ref([])
const selectedDocument = ref(null)
const selectedChunks = ref([])
const selectedFile = ref(null)
const loading = ref(false)
const chunksLoading = ref(false)
const uploading = ref(false)
const deleting = ref(false)
const retrying = ref(false)
const errorMessage = ref('')
const health = ref('checking')
const uploadTenantId = ref('default')
const uploadWorkspaceId = ref('default')
const uploadTags = ref('')
const uploadPermissions = ref('')
const retrievalQuery = ref('')
const retrievalStrategy = ref('hybrid')
const retrievalTopK = ref('')
const retrievalTenantId = ref('default')
const retrievalWorkspaceId = ref('default')
const retrievalTags = ref('')
const retrievalPrincipal = ref('')
const retrievalUseSelectedDocument = ref(false)
const retrievalLoading = ref(false)
const retrievalError = ref('')
const retrievalResult = ref(null)
const compareResult = ref(null)

let pollingTimer = null

const indexedCount = computed(() => documents.value.filter((item) => item.status === 'indexed').length)
const processingCount = computed(() =>
  documents.value.filter((item) => ['parsing', 'chunked', 'embedding'].includes(item.status)).length
)
const failedCount = computed(() =>
  documents.value.filter((item) => ['failed', 'embedding_failed'].includes(item.status)).length
)
const selectedEmbeddingSummary = computed(() => selectedDocument.value?.embedding_summary || {})
const selectedHasFailedEmbedding = computed(() =>
  selectedChunks.value.some((chunk) => chunk.embedding?.status === 'failed') ||
  selectedDocument.value?.status === 'embedding_failed'
)
const displayedRetrievalGroups = computed(() => {
  if (compareResult.value?.results) {
    return ['vector', 'keyword', 'hybrid'].map((strategy) => ({
      key: strategy,
      title: strategyLabel(strategy),
      result: compareResult.value.results[strategy]
    }))
  }
  if (retrievalResult.value) {
    return [
      {
        key: retrievalResult.value.strategy,
        title: strategyLabel(retrievalResult.value.strategy),
        result: retrievalResult.value
      }
    ]
  }
  return []
})

onMounted(async () => {
  await Promise.all([checkHealth(), fetchDocuments()])
  pollingTimer = window.setInterval(refreshActiveWork, 3000)
})

onUnmounted(() => {
  if (pollingTimer) {
    window.clearInterval(pollingTimer)
  }
})

async function checkHealth() {
  try {
    const response = await fetch('/api/health')
    health.value = response.ok ? 'ok' : 'error'
  } catch (error) {
    health.value = 'error'
  }
}

async function fetchDocuments() {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await fetch('/api/documents')
    if (!response.ok) {
      throw new Error('文档列表加载失败')
    }
    const data = await response.json()
    documents.value = data.items || []
    if (!selectedDocument.value && documents.value.length > 0) {
      await openDocument(documents.value[0].id)
    } else if (selectedDocument.value) {
      const refreshed = documents.value.find((item) => item.id === selectedDocument.value.id)
      if (refreshed) {
        selectedDocument.value = { ...selectedDocument.value, ...refreshed }
      }
    }
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    loading.value = false
  }
}

function pickFile(event) {
  selectedFile.value = event.target.files?.[0] || null
}

async function uploadDocument() {
  if (!selectedFile.value) {
    errorMessage.value = '请选择文件'
    return
  }

  uploading.value = true
  errorMessage.value = ''

  const form = new FormData()
  form.append('file', selectedFile.value)
  form.append('tenant_id', uploadTenantId.value.trim() || 'default')
  form.append('workspace_id', uploadWorkspaceId.value.trim() || 'default')
  if (uploadTags.value.trim()) {
    form.append('tags', uploadTags.value.trim())
  }
  if (uploadPermissions.value.trim()) {
    form.append('permissions', uploadPermissions.value.trim())
  }

  try {
    const response = await fetch('/api/documents/upload', {
      method: 'POST',
      headers: {
        'Idempotency-Key': makeIdempotencyKey()
      },
      body: form
    })
    const data = await response.json()
    if (!response.ok) {
      throw new Error(data.detail?.message || data.detail || '上传失败')
    }
    await fetchDocuments()
    await openDocument(data.id)
    selectedFile.value = null
    document.querySelector('#document-file').value = ''
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    uploading.value = false
  }
}

async function openDocument(id) {
  errorMessage.value = ''
  try {
    const response = await fetch(`/api/documents/${id}`)
    if (!response.ok) {
      throw new Error('文档详情加载失败')
    }
    selectedDocument.value = await response.json()
    await fetchChunks(id)
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function fetchChunks(id) {
  chunksLoading.value = true
  try {
    const response = await fetch(`/api/documents/${id}/chunks`)
    if (!response.ok) {
      throw new Error('chunk 加载失败')
    }
    const data = await response.json()
    selectedChunks.value = data.items || []
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    chunksLoading.value = false
  }
}

async function retryEmbedding() {
  if (!selectedDocument.value) return
  retrying.value = true
  errorMessage.value = ''

  try {
    const response = await fetch(`/api/documents/${selectedDocument.value.id}/embedding-jobs`, {
      method: 'POST'
    })
    const data = await response.json()
    if (!response.ok) {
      throw new Error(data.detail || '重试失败')
    }
    await openDocument(selectedDocument.value.id)
    await fetchDocuments()
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    retrying.value = false
  }
}

async function deleteSelected() {
  if (!selectedDocument.value) return
  deleting.value = true
  errorMessage.value = ''

  try {
    const response = await fetch(`/api/documents/${selectedDocument.value.id}`, {
      method: 'DELETE',
      headers: {
        'Idempotency-Key': makeIdempotencyKey()
      }
    })
    if (!response.ok) {
      throw new Error('删除失败')
    }
    selectedDocument.value = null
    selectedChunks.value = []
    await fetchDocuments()
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    deleting.value = false
  }
}

async function refreshActiveWork() {
  if (uploading.value || deleting.value || retrying.value) return
  const hasActiveDocument = documents.value.some((item) => shouldPoll(item))
  const selectedNeedsRefresh = selectedDocument.value && shouldPoll(selectedDocument.value)
  if (!hasActiveDocument && !selectedNeedsRefresh) return

  await fetchDocuments()
  if (selectedDocument.value && shouldPoll(selectedDocument.value)) {
    await openDocument(selectedDocument.value.id)
  }
}

function shouldPoll(documentItem) {
  const jobStatus = documentItem.embedding_job?.status
  return (
    ['parsing', 'chunked', 'embedding'].includes(documentItem.status) ||
    ['pending', 'queued', 'running'].includes(jobStatus)
  )
}

function makeIdempotencyKey() {
  if (crypto?.randomUUID) {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

async function runRetrieval() {
  await submitRetrieval('/api/retrieval/search', buildRetrievalPayload(retrievalStrategy.value), false)
}

async function compareRetrieval() {
  await submitRetrieval('/api/retrieval/compare', buildRetrievalPayload(), true)
}

async function submitRetrieval(url, payload, isCompare) {
  if (!payload.query) {
    retrievalError.value = '请输入检索问题'
    return
  }

  retrievalLoading.value = true
  retrievalError.value = ''
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload)
    })
    const data = await response.json()
    if (!response.ok) {
      throw new Error(data.detail || '检索失败')
    }
    if (isCompare) {
      compareResult.value = data
      retrievalResult.value = null
    } else {
      retrievalResult.value = data
      compareResult.value = null
    }
  } catch (error) {
    retrievalError.value = error.message
  } finally {
    retrievalLoading.value = false
  }
}

function buildRetrievalPayload(strategy = null) {
  const payload = {
    query: retrievalQuery.value.trim(),
    filters: buildRetrievalFilters()
  }
  const topK = Number(retrievalTopK.value)
  if (Number.isInteger(topK) && topK > 0) {
    payload.top_k = topK
  }
  if (strategy) {
    payload.strategy = strategy
  }
  return payload
}

function buildRetrievalFilters() {
  const filters = {}
  const tenant = retrievalTenantId.value.trim()
  const workspace = retrievalWorkspaceId.value.trim()
  const tags = splitList(retrievalTags.value)
  const principal = retrievalPrincipal.value.trim()

  if (tenant) filters.tenant_id = tenant
  if (workspace) filters.workspace_id = workspace
  if (tags.length > 0) filters.tags = tags
  if (principal) filters.principal = principal
  if (retrievalUseSelectedDocument.value && selectedDocument.value) {
    filters.document_id = selectedDocument.value.id
  }
  return filters
}

function splitList(value) {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

function strategyLabel(strategy) {
  const labels = {
    vector: 'Vector',
    keyword: 'Keyword',
    hybrid: 'Hybrid'
  }
  return labels[strategy] || strategy
}

function formatScore(score) {
  if (score === null || score === undefined) return '-'
  return Number(score).toFixed(4)
}

function statusLabel(status) {
  const labels = {
    uploaded: '已上传',
    parsing: '解析中',
    chunked: '已切片',
    embedding: '向量化中',
    completed: '已解析',
    indexed: '已入库',
    failed: '失败',
    embedding_failed: '向量化失败',
    pending: '等待中',
    queued: '排队中',
    running: '运行中',
    embedded: '已向量化'
  }
  return labels[status] || status
}

function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

function formatDate(value) {
  if (!value) return '-'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date(value))
}
</script>

<template>
  <main class="layout">
    <aside class="sidebar">
      <div class="brand">
        <div>
          <p class="eyebrow">Day 3</p>
          <h1>知识库</h1>
        </div>
        <span class="health" :class="health">{{ health }}</span>
      </div>

      <section class="panel upload-panel">
        <div class="upload-row">
          <label class="file-picker" for="document-file">
            <input
              id="document-file"
              type="file"
              accept=".pdf,.docx,.md,.markdown,.html,.htm"
              @change="pickFile"
            />
            <span>{{ selectedFile ? selectedFile.name : '选择文档' }}</span>
          </label>
          <button class="primary" :disabled="uploading || !selectedFile" @click="uploadDocument">
            {{ uploading ? '上传中' : '上传' }}
          </button>
        </div>
        <div class="form-grid upload-metadata">
          <label>
            <span>Tenant</span>
            <input v-model="uploadTenantId" type="text" placeholder="default" />
          </label>
          <label>
            <span>Workspace</span>
            <input v-model="uploadWorkspaceId" type="text" placeholder="default" />
          </label>
          <label class="wide-field">
            <span>Tags</span>
            <input v-model="uploadTags" type="text" placeholder="tag1, tag2" />
          </label>
          <label class="wide-field">
            <span>Permissions JSON</span>
            <textarea v-model="uploadPermissions" rows="2" placeholder='{"subjects":["team-a"]}'></textarea>
          </label>
        </div>
      </section>

      <section class="stats">
        <div>
          <strong>{{ documents.length }}</strong>
          <span>文档</span>
        </div>
        <div>
          <strong>{{ indexedCount }}</strong>
          <span>入库</span>
        </div>
        <div>
          <strong>{{ processingCount }}</strong>
          <span>处理中</span>
        </div>
        <div>
          <strong>{{ failedCount }}</strong>
          <span>失败</span>
        </div>
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
      <section class="panel retrieval-panel">
        <header class="retrieval-header">
          <div>
            <p class="eyebrow">Retrieval</p>
            <h2>检索</h2>
          </div>
          <div class="retrieval-actions">
            <button class="secondary" :disabled="retrievalLoading" @click="compareRetrieval">
              Compare
            </button>
            <button class="primary" :disabled="retrievalLoading" @click="runRetrieval">
              {{ retrievalLoading ? '检索中' : 'Search' }}
            </button>
          </div>
        </header>

        <div class="retrieval-form">
          <label class="query-field">
            <span>Query</span>
            <input v-model="retrievalQuery" type="text" placeholder="输入问题或关键词" @keyup.enter="runRetrieval" />
          </label>
          <label>
            <span>Strategy</span>
            <select v-model="retrievalStrategy">
              <option value="hybrid">Hybrid</option>
              <option value="vector">Vector</option>
              <option value="keyword">Keyword</option>
            </select>
          </label>
          <label>
            <span>Top K</span>
            <input v-model="retrievalTopK" type="number" min="1" max="50" placeholder="auto" />
          </label>
          <label>
            <span>Tenant</span>
            <input v-model="retrievalTenantId" type="text" placeholder="default" />
          </label>
          <label>
            <span>Workspace</span>
            <input v-model="retrievalWorkspaceId" type="text" placeholder="default" />
          </label>
          <label>
            <span>Tags</span>
            <input v-model="retrievalTags" type="text" placeholder="tag1, tag2" />
          </label>
          <label>
            <span>Principal</span>
            <input v-model="retrievalPrincipal" type="text" placeholder="user/team" />
          </label>
          <label class="checkbox-line">
            <input v-model="retrievalUseSelectedDocument" type="checkbox" :disabled="!selectedDocument" />
            <span>当前文档</span>
          </label>
        </div>

        <p v-if="retrievalError" class="error">{{ retrievalError }}</p>

        <div v-if="displayedRetrievalGroups.length" class="retrieval-results">
          <article v-for="group in displayedRetrievalGroups" :key="group.key" class="retrieval-group">
            <header class="group-header">
              <div>
                <h3>{{ group.title }}</h3>
                <p class="muted">
                  {{ group.result?.rewritten_query || '-' }} · top {{ group.result?.top_k || '-' }}
                </p>
              </div>
              <span v-if="group.result?.need_human_handoff" class="status failed">need_human_handoff</span>
              <span v-else class="status indexed">{{ group.result?.results?.length || 0 }} citations</span>
            </header>
            <p v-if="group.result?.diagnostics?.error" class="error">
              {{ group.result.diagnostics.error }}
            </p>
            <div v-else-if="!group.result" class="empty">暂无结果</div>
            <div v-else-if="group.result?.need_human_handoff" class="empty">未检索到可靠来源</div>
            <template v-else>
              <article v-for="item in group.result.results" :key="`${group.key}-${item.chunk_id}`" class="citation-row">
                <header>
                  <strong>{{ item.document_name }}</strong>
                  <span>Chunk {{ item.chunk_index + 1 }}</span>
                  <span v-if="item.page">Page {{ item.page }}</span>
                  <span v-if="item.heading">{{ item.heading }}</span>
                  <span>score {{ formatScore(item.score) }}</span>
                </header>
                <p>{{ item.snippet }}</p>
                <pre>{{ JSON.stringify(item.metadata, null, 2) }}</pre>
              </article>
            </template>
          </article>
        </div>
      </section>

      <div v-if="selectedDocument" class="detail">
        <header class="detail-header">
          <div>
            <p class="eyebrow">{{ selectedDocument.file_ext }}</p>
            <h2>{{ selectedDocument.filename }}</h2>
            <p class="muted">
              {{ selectedDocument.parser_name || '未解析' }} · {{ formatDate(selectedDocument.created_at) }}
            </p>
          </div>
          <div class="header-actions">
            <button
              v-if="selectedHasFailedEmbedding"
              class="secondary"
              :disabled="retrying"
              @click="retryEmbedding"
            >
              {{ retrying ? '重试中' : '重试向量化' }}
            </button>
            <button class="danger" :disabled="deleting" @click="deleteSelected">
              {{ deleting ? '删除中' : '删除' }}
            </button>
          </div>
        </header>

        <div class="detail-grid">
          <section class="panel">
            <h3>状态</h3>
            <dl>
              <div>
                <dt>文档状态</dt>
                <dd><span class="status" :class="selectedDocument.status">{{ statusLabel(selectedDocument.status) }}</span></dd>
              </div>
              <div>
                <dt>Chunk 数量</dt>
                <dd>{{ selectedDocument.chunk_count || 0 }}</dd>
              </div>
              <div>
                <dt>向量状态</dt>
                <dd class="summary-line">
                  <span>已完成 {{ selectedEmbeddingSummary.embedded || 0 }}</span>
                  <span>处理中 {{ selectedEmbeddingSummary.embedding || 0 }}</span>
                  <span>等待 {{ selectedEmbeddingSummary.pending || 0 }}</span>
                  <span>失败 {{ selectedEmbeddingSummary.failed || 0 }}</span>
                </dd>
              </div>
              <div>
                <dt>SHA-256</dt>
                <dd class="hash">{{ selectedDocument.source_hash }}</dd>
              </div>
              <div>
                <dt>检索过滤</dt>
                <dd class="summary-line">
                  <span>{{ selectedDocument.tenant_id || 'default' }}</span>
                  <span>{{ selectedDocument.workspace_id || 'default' }}</span>
                  <span>{{ (selectedDocument.tags || []).join(', ') || 'no tags' }}</span>
                </dd>
              </div>
            </dl>
          </section>

          <section class="panel">
            <h3>元数据</h3>
            <pre>{{ JSON.stringify(selectedDocument.metadata, null, 2) }}</pre>
          </section>
        </div>

        <section v-if="selectedDocument.error_message" class="panel error-panel">
          <h3>错误</h3>
          <pre>{{ selectedDocument.error_message }}</pre>
        </section>

        <section class="panel text-panel">
          <h3>文本预览</h3>
          <pre>{{ selectedDocument.text_preview || '无文本' }}</pre>
        </section>

        <section class="panel chunk-panel">
          <h3>Chunks</h3>
          <div v-if="chunksLoading" class="empty">加载 chunk 中</div>
          <div v-else-if="selectedChunks.length === 0" class="empty">暂无 chunk</div>
          <template v-else>
            <article v-for="chunk in selectedChunks" :key="chunk.id" class="chunk-row">
              <header class="chunk-header">
                <div>
                  <strong>Chunk {{ chunk.chunk_index + 1 }}</strong>
                  <span v-if="chunk.page">Page {{ chunk.page }}</span>
                  <span v-if="chunk.heading">{{ chunk.heading }}</span>
                </div>
                <span class="status" :class="chunk.embedding.status">
                  {{ statusLabel(chunk.embedding.status) }}
                </span>
              </header>
              <pre class="chunk-text">{{ chunk.text }}</pre>
              <div class="chunk-meta">
                <span>{{ chunk.embedding.model || '未生成模型' }}</span>
                <span>{{ chunk.embedding.dim || '-' }} dim</span>
                <span>{{ chunk.embedding.has_vector ? 'vector ready' : 'no vector' }}</span>
              </div>
              <pre class="chunk-json">{{ JSON.stringify(chunk.metadata, null, 2) }}</pre>
            </article>
          </template>
        </section>
      </div>

      <div v-else class="placeholder">
        <h2>知识库文档</h2>
        <p>等待上传</p>
      </div>
    </section>
  </main>
</template>

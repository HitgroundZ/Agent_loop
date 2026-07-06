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
          <p class="eyebrow">Day 2</p>
          <h1>知识库</h1>
        </div>
        <span class="health" :class="health">{{ health }}</span>
      </div>

      <section class="panel upload-panel">
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

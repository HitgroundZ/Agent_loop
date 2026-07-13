import { computed, onMounted, onUnmounted, ref } from 'vue'

import { apiRequest, deleteJson, getJson, postJson } from '../services/api'


export function useKnowledgeWorkspace() {
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
  const agentQuestion = ref('')
  const agentUserId = ref('demo-user')
  const agentSessionId = ref('')
  const agentLoading = ref(false)
  const agentError = ref('')
  const agentResult = ref(null)
  const agentTraceExpanded = ref(false)

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
      return [{
        key: retrievalResult.value.strategy,
        title: strategyLabel(retrievalResult.value.strategy),
        result: retrievalResult.value
      }]
    }
    return []
  })
  const agentTracePreview = computed(() => agentResult.value?.trace_preview || [])
  const agentCitations = computed(() => agentResult.value?.citations || [])
  const agentMemoryContext = computed(() => agentResult.value?.memory_context || [])
  const agentSessionMessages = computed(() => agentResult.value?.session_state?.recent_messages || [])
  const agentShortTermState = computed(() => agentResult.value?.session_state || {})

  onMounted(async () => {
    await Promise.all([checkHealth(), fetchDocuments()])
    pollingTimer = window.setInterval(refreshActiveWork, 3000)
  })

  onUnmounted(() => {
    if (pollingTimer) window.clearInterval(pollingTimer)
  })

  async function checkHealth() {
    try {
      await getJson('/api/health')
      health.value = 'ok'
    } catch {
      health.value = 'error'
    }
  }

  async function fetchDocuments() {
    loading.value = true
    errorMessage.value = ''
    try {
      const data = await getJson('/api/documents')
      documents.value = data.items || []
      if (!selectedDocument.value && documents.value.length > 0) {
        await openDocument(documents.value[0].id)
      } else if (selectedDocument.value) {
        const refreshed = documents.value.find((item) => item.id === selectedDocument.value.id)
        if (refreshed) selectedDocument.value = { ...selectedDocument.value, ...refreshed }
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
    if (uploadTags.value.trim()) form.append('tags', uploadTags.value.trim())
    if (uploadPermissions.value.trim()) form.append('permissions', uploadPermissions.value.trim())

    try {
      const data = await apiRequest('/api/documents/upload', {
        method: 'POST',
        headers: { 'Idempotency-Key': makeIdempotencyKey() },
        body: form
      })
      await fetchDocuments()
      await openDocument(data.id)
      selectedFile.value = null
      const fileInput = document.querySelector('#document-file, #document-file-module')
      if (fileInput) fileInput.value = ''
    } catch (error) {
      errorMessage.value = error.message
    } finally {
      uploading.value = false
    }
  }

  async function openDocument(id) {
    errorMessage.value = ''
    try {
      selectedDocument.value = await getJson(`/api/documents/${id}`)
      await fetchChunks(id)
    } catch (error) {
      errorMessage.value = error.message
    }
  }

  async function fetchChunks(id) {
    chunksLoading.value = true
    try {
      const data = await getJson(`/api/documents/${id}/chunks`)
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
      await apiRequest(`/api/documents/${selectedDocument.value.id}/embedding-jobs`, { method: 'POST' })
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
      await deleteJson(`/api/documents/${selectedDocument.value.id}`, {
        'Idempotency-Key': makeIdempotencyKey()
      })
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
    return ['parsing', 'chunked', 'embedding'].includes(documentItem.status) ||
      ['pending', 'queued', 'running'].includes(jobStatus)
  }

  function makeIdempotencyKey() {
    if (crypto?.randomUUID) return crypto.randomUUID()
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`
  }

  async function runRetrieval() {
    await submitRetrieval('/api/retrieval/search', buildRetrievalPayload(retrievalStrategy.value), false)
  }

  async function compareRetrieval() {
    await submitRetrieval('/api/retrieval/compare', buildRetrievalPayload(), true)
  }

  async function runAgent() {
    const question = agentQuestion.value.trim() || retrievalQuery.value.trim()
    if (!question) {
      agentError.value = '请输入问题'
      return
    }
    if (!agentUserId.value.trim()) {
      agentError.value = '请输入用户 ID，以便隔离并召回长期记忆'
      return
    }
    agentLoading.value = true
    agentError.value = ''
    try {
      const payload = {
        question,
        user_id: agentUserId.value.trim(),
        strategy: retrievalStrategy.value,
        filters: buildRetrievalFilters(),
        auto_approve: true
      }
      const topK = Number(retrievalTopK.value)
      if (Number.isInteger(topK) && topK > 0) payload.top_k = topK
      const sessionId = agentSessionId.value.trim()
      if (sessionId) payload.session_id = sessionId
      const data = await postJson('/api/agent/runs', payload)
      agentResult.value = data
      agentSessionId.value = data.session_id || sessionId
      agentQuestion.value = question
    } catch (error) {
      agentError.value = error.message
    } finally {
      agentLoading.value = false
    }
  }

  async function submitRetrieval(url, payload, isCompare) {
    if (!payload.query) {
      retrievalError.value = '请输入检索问题'
      return
    }
    retrievalLoading.value = true
    retrievalError.value = ''
    try {
      const data = await postJson(url, payload)
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
    const payload = { query: retrievalQuery.value.trim(), filters: buildRetrievalFilters() }
    const topK = Number(retrievalTopK.value)
    if (Number.isInteger(topK) && topK > 0) payload.top_k = topK
    if (strategy) payload.strategy = strategy
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
    return value.split(',').map((item) => item.trim()).filter(Boolean)
  }

  function strategyLabel(strategy) {
    return { vector: '向量检索', keyword: '关键词检索', hybrid: '混合检索' }[strategy] || strategy
  }

  function formatScore(score) {
    if (score === null || score === undefined) return '-'
    return Number(score).toFixed(4)
  }

  function formatDuration(ms) {
    if (ms === null || ms === undefined) return '-'
    if (ms < 1000) return `${ms} ms`
    return `${(ms / 1000).toFixed(2)} s`
  }

  function formatTokens(tokens) {
    if (!tokens) return '0 个令牌'
    return `${tokens.total_tokens || 0} 个令牌`
  }

  function agentStateClass(state) {
    return state || 'unknown'
  }

  function agentStateLabel(state) {
    return {
      created: '已创建', analyzing: '分析中', recalling: '召回记忆', retrieving: '检索中',
      acting: '生成回答', waiting_approval: '等待审批', evaluating: '评估中',
      completed: '已完成', failed: '失败', escalated_to_human: '转人工'
    }[state] || state
  }

  function memoryCategoryLabel(category) {
    return {
      event_summary: '事件摘要', scene: '场景记忆', user_profile: '用户画像',
      human_correction: '人工纠错'
    }[category] || category
  }

  function roleLabel(role) {
    return { user: '用户', assistant: '助手' }[role] || role
  }

  function statusLabel(status) {
    return {
      uploaded: '已上传', parsing: '解析中', chunked: '已切片', embedding: '向量化中',
      completed: '已解析', indexed: '已入库', failed: '失败', embedding_failed: '向量化失败',
      pending: '等待中', queued: '排队中', running: '运行中', embedded: '已向量化'
    }[status] || status
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
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
    }).format(new Date(value))
  }

  return {
    documents, selectedDocument, selectedChunks, selectedFile, loading, chunksLoading,
    uploading, deleting, retrying, errorMessage, health, uploadTenantId, uploadWorkspaceId,
    uploadTags, uploadPermissions, retrievalQuery, retrievalStrategy, retrievalTopK,
    retrievalTenantId, retrievalWorkspaceId, retrievalTags, retrievalPrincipal,
    retrievalUseSelectedDocument, retrievalLoading, retrievalError, retrievalResult,
    compareResult, agentQuestion, agentUserId, agentSessionId, agentLoading, agentError,
    agentResult, agentTraceExpanded, indexedCount, processingCount, failedCount,
    selectedEmbeddingSummary, selectedHasFailedEmbedding, displayedRetrievalGroups,
    agentTracePreview, agentCitations, agentMemoryContext, agentSessionMessages,
    agentShortTermState, pickFile, uploadDocument, openDocument, retryEmbedding,
    deleteSelected, runRetrieval, compareRetrieval, runAgent, strategyLabel, formatScore,
    formatDuration, formatTokens, agentStateClass, agentStateLabel, memoryCategoryLabel,
    roleLabel, statusLabel, formatBytes, formatDate
  }
}

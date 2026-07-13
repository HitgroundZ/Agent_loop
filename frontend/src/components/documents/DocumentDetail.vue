<script setup>
import { ref } from 'vue'


defineProps({
  document: { type: Object, required: true },
  chunks: { type: Array, default: () => [] },
  chunksLoading: { type: Boolean, default: false },
  embeddingSummary: { type: Object, default: () => ({}) },
  hasFailedEmbedding: { type: Boolean, default: false },
  retrying: { type: Boolean, default: false },
  deleting: { type: Boolean, default: false },
  statusLabel: { type: Function, required: true },
  formatDate: { type: Function, required: true }
})

defineEmits(['retry', 'delete'])

const activeTab = ref('summary')
const tabs = [
  { id: 'summary', label: '概览' },
  { id: 'text', label: '文本预览' },
  { id: 'chunks', label: '切片' }
]
</script>

<template>
  <article class="document-detail-module">
    <header class="document-detail-header">
      <div>
        <span class="file-type-badge">{{ document.file_ext }}</span>
        <h2>{{ document.filename }}</h2>
        <p>{{ document.parser_name || '未解析' }} · {{ formatDate(document.created_at) }}</p>
      </div>
      <div class="header-actions">
        <button v-if="hasFailedEmbedding" class="secondary" :disabled="retrying" @click="$emit('retry')">{{ retrying ? '重试中' : '重试向量化' }}</button>
        <button class="danger" :disabled="deleting" @click="$emit('delete')">{{ deleting ? '删除中' : '删除文档' }}</button>
      </div>
    </header>

    <nav class="content-tabs document-tabs" aria-label="文档详情导航">
      <button v-for="tab in tabs" :key="tab.id" :class="{ active: activeTab === tab.id }" @click="activeTab = tab.id">
        {{ tab.label }}<span v-if="tab.id === 'chunks'">{{ chunks.length }}</span>
      </button>
    </nav>

    <div class="document-tab-content">
      <template v-if="activeTab === 'summary'">
        <section class="document-summary-grid">
          <article class="summary-card">
            <span class="section-index">处理状态</span>
            <dl>
              <div><dt>文档状态</dt><dd><span class="status" :class="document.status">{{ statusLabel(document.status) }}</span></dd></div>
              <div><dt>切片数量</dt><dd>{{ document.chunk_count || 0 }}</dd></div>
              <div><dt>向量状态</dt><dd class="summary-line"><span>完成 {{ embeddingSummary.embedded || 0 }}</span><span>处理中 {{ embeddingSummary.embedding || 0 }}</span><span>等待 {{ embeddingSummary.pending || 0 }}</span><span>失败 {{ embeddingSummary.failed || 0 }}</span></dd></div>
              <div><dt>租户 / 工作区</dt><dd>{{ document.tenant_id || 'default' }} / {{ document.workspace_id || 'default' }}</dd></div>
              <div><dt>标签</dt><dd>{{ (document.tags || []).join(', ') || '无标签' }}</dd></div>
              <div><dt>SHA-256</dt><dd class="hash">{{ document.source_hash }}</dd></div>
            </dl>
          </article>
          <article class="summary-card metadata-card">
            <span class="section-index">解析元数据</span>
            <pre>{{ JSON.stringify(document.metadata, null, 2) }}</pre>
          </article>
        </section>
        <section v-if="document.error_message" class="document-error-card"><strong>处理错误</strong><pre>{{ document.error_message }}</pre></section>
      </template>

      <template v-else-if="activeTab === 'text'">
        <section class="document-text-viewer">
          <div class="viewer-toolbar"><span>解析文本预览</span><small>{{ document.text_preview?.length || 0 }} 字符</small></div>
          <pre>{{ document.text_preview || '无文本' }}</pre>
        </section>
      </template>

      <template v-else>
        <div v-if="chunksLoading" class="empty spacious-empty">正在加载切片</div>
        <div v-else-if="chunks.length === 0" class="empty spacious-empty">暂无切片</div>
        <section v-else class="document-chunk-list">
          <article v-for="chunk in chunks" :key="chunk.id" class="chunk-row module-chunk-row">
            <header class="chunk-header">
              <div><strong>切片 {{ chunk.chunk_index + 1 }}</strong><span v-if="chunk.page">第 {{ chunk.page }} 页</span><span v-if="chunk.heading">{{ chunk.heading }}</span></div>
              <span class="status" :class="chunk.embedding.status">{{ statusLabel(chunk.embedding.status) }}</span>
            </header>
            <pre class="chunk-text">{{ chunk.text }}</pre>
            <div class="chunk-meta"><span>{{ chunk.embedding.model || '未生成模型' }}</span><span>{{ chunk.embedding.dim || '-' }} 维</span><span>{{ chunk.embedding.has_vector ? '向量已生成' : '暂无向量' }}</span></div>
            <details><summary>查看 metadata</summary><pre class="chunk-json">{{ JSON.stringify(chunk.metadata, null, 2) }}</pre></details>
          </article>
        </section>
      </template>
    </div>
  </article>
</template>

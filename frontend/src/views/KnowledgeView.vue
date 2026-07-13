<script setup>
import { ref } from 'vue'

import DocumentDetail from '../components/documents/DocumentDetail.vue'
import { useWorkspaceContext } from '../composables/workspaceContext'


const {
  documents, selectedDocument, selectedChunks, selectedFile, loading, chunksLoading,
  uploading, deleting, retrying, errorMessage, uploadTenantId, uploadWorkspaceId,
  uploadTags, uploadPermissions, indexedCount, processingCount, failedCount,
  selectedEmbeddingSummary, selectedHasFailedEmbedding, pickFile, uploadDocument,
  openDocument, retryEmbedding, deleteSelected, statusLabel, formatBytes, formatDate
} = useWorkspaceContext()

const showUpload = ref(false)
</script>

<template>
  <div class="module-page knowledge-page">
    <aside class="module-card document-library-pane">
      <header class="library-header">
        <div><span class="section-index">文档库</span><h2>全部文档</h2></div>
        <button class="primary compact-button" @click="showUpload = !showUpload">{{ showUpload ? '收起' : '上传' }}</button>
      </header>

      <form v-if="showUpload" class="inline-upload-form" @submit.prevent="uploadDocument">
        <label class="file-picker" for="document-file-module">
          <input id="document-file-module" type="file" accept=".pdf,.docx,.md,.markdown,.html,.htm" @change="pickFile" />
          <span>{{ selectedFile ? selectedFile.name : '选择 PDF、DOCX、Markdown 或 HTML' }}</span>
        </label>
        <div class="upload-field-grid">
          <label><span>租户</span><input v-model="uploadTenantId" type="text" /></label>
          <label><span>工作区</span><input v-model="uploadWorkspaceId" type="text" /></label>
          <label class="wide-field"><span>标签</span><input v-model="uploadTags" type="text" placeholder="标签1, 标签2" /></label>
          <label class="wide-field"><span>权限 JSON</span><textarea v-model="uploadPermissions" rows="2" placeholder='{"subjects":["team-a"]}'></textarea></label>
        </div>
        <button class="primary" type="submit" :disabled="uploading || !selectedFile">{{ uploading ? '上传中' : '上传并解析' }}</button>
      </form>

      <section class="library-stats">
        <span><strong>{{ documents.length }}</strong>全部</span>
        <span><strong>{{ indexedCount }}</strong>已入库</span>
        <span><strong>{{ processingCount }}</strong>处理中</span>
        <span><strong>{{ failedCount }}</strong>失败</span>
      </section>
      <p v-if="errorMessage" class="error library-error">{{ errorMessage }}</p>

      <section class="module-document-list" aria-label="文档列表">
        <button v-for="item in documents" :key="item.id" :class="{ active: selectedDocument?.id === item.id }" @click="openDocument(item.id)">
          <span class="document-icon">{{ item.file_ext?.replace('.', '').toUpperCase() || 'DOC' }}</span>
          <span class="document-row-copy"><strong>{{ item.filename }}</strong><small><span class="status" :class="item.status">{{ statusLabel(item.status) }}</span>{{ item.chunk_count || 0 }} chunks · {{ formatBytes(item.size_bytes) }}</small></span>
        </button>
        <div v-if="loading" class="empty">加载文档中</div>
        <div v-else-if="documents.length === 0" class="empty">暂无文档</div>
      </section>
    </aside>

    <section class="module-card document-detail-pane">
      <DocumentDetail
        v-if="selectedDocument"
        :document="selectedDocument"
        :chunks="selectedChunks"
        :chunks-loading="chunksLoading"
        :embedding-summary="selectedEmbeddingSummary"
        :has-failed-embedding="selectedHasFailedEmbedding"
        :retrying="retrying"
        :deleting="deleting"
        :status-label="statusLabel"
        :format-date="formatDate"
        @retry="retryEmbedding"
        @delete="deleteSelected"
      />
      <div v-else class="empty-workspace document-empty">
        <span class="empty-illustration">▤</span><h3>选择一个文档</h3><p>文档概览、文本和切片会在这里分标签显示。</p>
      </div>
    </section>
  </div>
</template>

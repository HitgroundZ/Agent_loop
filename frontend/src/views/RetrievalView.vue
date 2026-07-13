<script setup>
import { useWorkspaceContext } from '../composables/workspaceContext'


const {
  selectedDocument, retrievalQuery, retrievalStrategy, retrievalTopK, retrievalTenantId,
  retrievalWorkspaceId, retrievalTags, retrievalPrincipal, retrievalUseSelectedDocument,
  retrievalLoading, retrievalError, displayedRetrievalGroups, runRetrieval,
  compareRetrieval, formatScore
} = useWorkspaceContext()
</script>

<template>
  <div class="module-page retrieval-page">
    <section class="module-card retrieval-console">
      <div class="retrieval-query-block">
        <span class="section-index">检索输入</span>
        <label>
          <span>问题或关键词</span>
          <textarea v-model="retrievalQuery" rows="3" placeholder="输入要检索的内容" @keydown.ctrl.enter="runRetrieval"></textarea>
        </label>
      </div>
      <div class="retrieval-settings-grid">
        <label><span>策略</span><select v-model="retrievalStrategy"><option value="hybrid">混合检索</option><option value="vector">向量检索</option><option value="keyword">关键词检索</option></select></label>
        <label><span>返回数量</span><input v-model="retrievalTopK" type="number" min="1" max="50" placeholder="自动" /></label>
        <label><span>租户</span><input v-model="retrievalTenantId" type="text" placeholder="default" /></label>
        <label><span>工作区</span><input v-model="retrievalWorkspaceId" type="text" placeholder="default" /></label>
        <label><span>标签</span><input v-model="retrievalTags" type="text" placeholder="标签1, 标签2" /></label>
        <label><span>身份</span><input v-model="retrievalPrincipal" type="text" placeholder="用户/团队" /></label>
        <label class="checkbox-line filter-checkbox"><input v-model="retrievalUseSelectedDocument" type="checkbox" :disabled="!selectedDocument" /><span>限定当前文档</span></label>
      </div>
      <div class="retrieval-command-bar">
        <p>{{ selectedDocument ? `当前文档：${selectedDocument.filename}` : '未限定文档范围' }}</p>
        <button class="secondary" :disabled="retrievalLoading" @click="compareRetrieval">三种策略对比</button>
        <button class="primary" :disabled="retrievalLoading" @click="runRetrieval">{{ retrievalLoading ? '检索中' : '开始检索' }}</button>
      </div>
      <p v-if="retrievalError" class="error">{{ retrievalError }}</p>
    </section>

    <section class="retrieval-result-workspace">
      <div v-if="displayedRetrievalGroups.length === 0" class="module-card empty-workspace">
        <span class="empty-illustration">⌕</span><h3>等待检索</h3><p>结果会按策略分栏显示，便于比较召回数量和引用质量。</p>
      </div>
      <article v-for="group in displayedRetrievalGroups" :key="group.key" class="module-card retrieval-group module-retrieval-group">
        <header class="group-header">
          <div><span class="section-index">{{ group.key }}</span><h3>{{ group.title }}</h3><p class="muted">{{ group.result?.rewritten_query || '-' }} · Top {{ group.result?.top_k || '-' }}</p></div>
          <span v-if="group.result?.need_human_handoff" class="status failed">需人工处理</span>
          <span v-else class="status indexed">{{ group.result?.results?.length || 0 }} 条引用</span>
        </header>
        <p v-if="group.result?.diagnostics?.error" class="error">{{ group.result.diagnostics.error }}</p>
        <div v-else-if="!group.result || group.result?.need_human_handoff" class="empty">未检索到可靠来源</div>
        <div v-else class="retrieval-citation-list">
          <article v-for="item in group.result.results" :key="`${group.key}-${item.chunk_id}`" class="citation-row">
            <header><strong>{{ item.document_name }}</strong><span>切片 {{ item.chunk_index + 1 }}</span><span v-if="item.page">第 {{ item.page }} 页</span><span>得分 {{ formatScore(item.score) }}</span></header>
            <p>{{ item.snippet }}</p>
            <details><summary>查看 metadata</summary><pre>{{ JSON.stringify(item.metadata, null, 2) }}</pre></details>
          </article>
        </div>
      </article>
    </section>
  </div>
</template>

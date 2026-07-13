<script setup>
import { useWorkspaceContext } from '../composables/workspaceContext'


defineEmits(['navigate'])

const {
  documents, indexedCount, processingCount, failedCount, agentResult,
  selectedDocument, health, agentUserId, formatDate, statusLabel
} = useWorkspaceContext()

const workflows = [
  { id: 'agent', step: '01', title: '运行智能体', description: '带着用户记忆与知识库上下文完成一次可追溯问答。' },
  { id: 'memory', step: '02', title: '治理长期记忆', description: '核对来源、纠正事实，或禁用不应进入上下文的记忆。' },
  { id: 'knowledge', step: '03', title: '维护知识库', description: '上传资料，检查解析、切片与向量化状态。' },
  { id: 'retrieval', step: '04', title: '验证检索策略', description: '独立比较向量、关键词和混合检索效果。' }
]
</script>

<template>
  <div class="module-page overview-page">
    <section class="overview-hero">
      <div>
        <p class="eyebrow">Agent Loop · Day 5</p>
        <h2>把智能体、记忆与知识库拆成清晰的工作台</h2>
        <p>当前用户 <strong>{{ agentUserId }}</strong> 的操作会在各模块间保持一致，切换页面不会丢失上下文。</p>
      </div>
      <button class="primary hero-action" @click="$emit('navigate', 'agent')">开始一次问答</button>
    </section>

    <section class="overview-metrics">
      <article>
        <span>后端服务</span>
        <strong :class="health">{{ health === 'ok' ? '在线' : '异常' }}</strong>
        <small>FastAPI / PostgreSQL / Redis</small>
      </article>
      <article><span>知识库文档</span><strong>{{ documents.length }}</strong><small>{{ indexedCount }} 份已完成索引</small></article>
      <article><span>处理中</span><strong>{{ processingCount }}</strong><small>解析、切片或向量化</small></article>
      <article><span>异常任务</span><strong>{{ failedCount }}</strong><small>需要人工检查</small></article>
    </section>

    <section class="overview-grid">
      <article class="overview-panel workflow-panel">
        <header><div><span class="section-index">工作流</span><h3>选择一个模块开始</h3></div></header>
        <div class="workflow-list">
          <button v-for="item in workflows" :key="item.id" @click="$emit('navigate', item.id)">
            <span class="workflow-step">{{ item.step }}</span>
            <span><strong>{{ item.title }}</strong><small>{{ item.description }}</small></span>
            <i>→</i>
          </button>
        </div>
      </article>

      <article class="overview-panel activity-panel">
        <header><div><span class="section-index">最近状态</span><h3>工作区快照</h3></div></header>
        <dl class="activity-list">
          <div>
            <dt>最近智能体运行</dt>
            <dd v-if="agentResult"><span class="status" :class="agentResult.status">{{ agentResult.status }}</span>{{ formatDate(agentResult.updated_at) }}</dd>
            <dd v-else>尚未运行</dd>
          </div>
          <div><dt>当前文档</dt><dd>{{ selectedDocument?.filename || '尚未选择' }}</dd></div>
          <div><dt>当前用户</dt><dd>{{ agentUserId }}</dd></div>
        </dl>
        <div class="recent-documents">
          <h4>最近文档</h4>
          <button v-for="item in documents.slice(0, 4)" :key="item.id" @click="$emit('navigate', 'knowledge')">
            <span>{{ item.filename }}</span><small>{{ statusLabel(item.status) }}</small>
          </button>
          <p v-if="documents.length === 0" class="empty">暂无文档</p>
        </div>
      </article>
    </section>
  </div>
</template>

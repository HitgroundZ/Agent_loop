<script setup>
import { ref } from 'vue'

import { useWorkspaceContext } from '../composables/workspaceContext'


const {
  agentQuestion, agentSessionId, agentRetrievalMode, agentLoading, agentError,
  agentResult, agentTraceExpanded,
  agentTracePreview, agentCitations, agentMemoryContext, agentSessionMessages,
  agentShortTermState, agentToolActions, agentRoutingDecision, runAgent,
  formatScore, formatDuration, formatTokens,
  agentStateClass, agentStateLabel, memoryCategoryLabel, roleLabel
} = useWorkspaceContext()

const activeResultTab = ref('answer')
const resultTabs = [
  { id: 'answer', label: '回答与引用' },
  { id: 'memory', label: '本轮记忆' },
  { id: 'tools', label: '工具与审批' },
  { id: 'trace', label: '执行轨迹' },
  { id: 'session', label: '会话缓存' }
]

function toolStatusLabel(status) {
  return {
    proposed: '已提出', pending: '待审批', running: '执行中', executed: '已执行',
    rejected: '已拒绝', failed: '失败', blocked: '已阻断'
  }[status] || status
}

function retrievalDecisionLabel(value) {
  return {
    pending: '待决策', skipped: '已跳过', executed: '已检索', failed: '检索失败'
  }[value] || value || '未记录'
}
</script>

<template>
  <div class="module-page agent-page">
    <section class="module-card agent-composer">
      <div class="composer-copy">
        <span class="section-index">智能体入口</span>
        <h2>发起一次可追溯问答</h2>
        <p>模型按需选择记忆或知识库工具；安全策略会在副作用执行前校验权限与风险。</p>
      </div>
      <div class="composer-fields">
        <label class="question-field">
          <span>问题</span>
          <textarea v-model="agentQuestion" rows="3" placeholder="输入问题；按 Ctrl + Enter 快速提交。" @keydown.ctrl.enter="runAgent"></textarea>
        </label>
        <label>
          <span>会话 ID</span>
          <input v-model="agentSessionId" type="text" placeholder="留空时创建新会话" />
        </label>
        <label>
          <span>知识库模式</span>
          <select v-model="agentRetrievalMode">
            <option value="auto">Auto · 模型判断</option>
            <option value="always">Always · 强制检索</option>
            <option value="never">Never · 禁止检索</option>
          </select>
        </label>
        <button class="primary run-agent-button" :disabled="agentLoading" @click="runAgent">
          {{ agentLoading ? '正在运行' : '运行智能体' }}
        </button>
      </div>
      <p v-if="agentError" class="error">{{ agentError }}</p>
    </section>

    <section v-if="agentResult" class="module-card agent-run-workspace">
      <header class="run-summary-header">
        <div>
          <span class="status" :class="agentStateClass(agentResult.current_state)">{{ agentStateLabel(agentResult.current_state) }}</span>
          <h2>运行结果</h2>
          <p>{{ agentResult.id }} · 会话 {{ agentResult.session_id }}</p>
        </div>
        <div class="run-summary-metrics">
          <span><small>令牌</small><strong>{{ agentResult.token_usage?.total_tokens || 0 }}</strong></span>
          <span><small>重试</small><strong>{{ agentResult.retry_count || 0 }}</strong></span>
          <span><small>引用</small><strong>{{ agentCitations.length }}</strong></span>
          <span><small>记忆</small><strong>{{ agentMemoryContext.length }}</strong></span>
        </div>
      </header>

      <div class="state-flow compact-state-flow">
        <span v-for="(state, index) in agentResult.state_flow" :key="`${state}-${index}`" class="state-chip" :class="agentStateClass(state)">
          {{ index + 1 }} {{ agentStateLabel(state) }}
        </span>
      </div>

      <section class="short-term-grid" aria-label="Redis 短期记忆状态">
        <div><span>检索缓存</span><strong>{{ agentShortTermState.retrieval_cache?.hit ? '命中' : '未命中' }}</strong></div>
        <div><span>Pending approval</span><strong>{{ agentShortTermState.pending_approval?.status || '无' }}</strong></div>
        <div><span>Rate limit</span><strong>{{ agentShortTermState.rate_limit?.remaining ?? '-' }} / {{ agentShortTermState.rate_limit?.limit ?? '-' }}</strong></div>
        <div><span>Token budget</span><strong>{{ agentShortTermState.token_budget?.remaining ?? '-' }} / {{ agentShortTermState.token_budget?.limit ?? '-' }}</strong></div>
      </section>

      <section class="routing-decision-strip">
        <div>
          <span>路由来源</span>
          <strong>{{ agentRoutingDecision.source || '-' }}</strong>
        </div>
        <div>
          <span>知识库决策</span>
          <strong>{{ retrievalDecisionLabel(agentRoutingDecision.knowledge_retrieval) }}</strong>
        </div>
        <div class="routing-reason">
          <span>决策理由</span>
          <strong>{{ agentRoutingDecision.reason || '暂无' }}</strong>
        </div>
      </section>

      <div v-if="agentResult.current_state === 'waiting_approval'" class="waiting-approval-banner">
        <strong>运行已暂停，等待人工审批</strong>
        <span>请前往“审批台”处理 {{ agentToolActions.filter((item) => item.status === 'pending').length }} 个 action；全部处理后会自动续跑。</span>
      </div>

      <nav class="content-tabs" aria-label="运行结果导航">
        <button v-for="tab in resultTabs" :key="tab.id" :class="{ active: activeResultTab === tab.id }" @click="activeResultTab = tab.id">
          {{ tab.label }}
        </button>
      </nav>

      <div class="tab-content agent-tab-content">
        <template v-if="activeResultTab === 'answer'">
          <section class="answer-layout">
            <article class="answer-card">
              <span class="section-index">最终回答</span>
              <pre>{{ agentResult.answer || '暂无回答' }}</pre>
            </article>
            <article class="citation-card">
              <header><span class="section-index">引用来源</span><strong>{{ agentCitations.length }} 条</strong></header>
              <div v-if="agentCitations.length === 0" class="empty">暂无引用</div>
              <div v-else class="citation-list-scroll">
                <article v-for="item in agentCitations" :key="item.id" class="citation-row compact-citation">
                  <header>
                    <strong>{{ item.label }} {{ item.document_name }}</strong>
                    <template v-if="item.retrieval_source === 'memory'">
                      <span>{{ memoryCategoryLabel(item.memory_category) }}</span>
                      <span v-if="item.source_message_id">消息 {{ item.source_message_id }}</span>
                    </template>
                    <template v-else><span>切片 {{ (item.chunk_index ?? 0) + 1 }}</span></template>
                    <span>得分 {{ formatScore(item.score) }}</span>
                  </header>
                  <p>{{ item.snippet }}</p>
                </article>
              </div>
            </article>
          </section>
        </template>

        <template v-else-if="activeResultTab === 'memory'">
          <div v-if="agentMemoryContext.length === 0" class="empty spacious-empty">本轮没有向 prompt 注入长期记忆</div>
          <div v-else class="memory-injection-list standalone-list">
            <article v-for="memory in agentMemoryContext" :key="memory.id">
              <strong>{{ memoryCategoryLabel(memory.category) }}</strong>
              <span>{{ memory.content }}</span>
              <small>{{ memory.id }} · source_message_id={{ memory.source_message_id || '-' }}</small>
            </article>
          </div>
        </template>

        <template v-else-if="activeResultTab === 'trace'">
          <div class="trace-toolbar">
            <span>共 {{ agentTracePreview.length }} 个状态事件</span>
            <button class="secondary compact-button" @click="agentTraceExpanded = !agentTraceExpanded">
              {{ agentTraceExpanded ? '收起 JSON' : '查看完整 JSON' }}
            </button>
          </div>
          <div class="trace-list trace-module-list">
            <article v-for="event in agentTracePreview" :key="event.sequence" class="trace-row">
              <span class="state-chip" :class="agentStateClass(event.state)">{{ event.sequence }} {{ agentStateLabel(event.state) }}</span>
              <span>{{ event.output_summary }}</span>
              <span>{{ formatDuration(event.duration_ms) }}</span>
              <span>{{ formatTokens(event.token_usage) }}</span>
              <span v-if="event.error" class="trace-error">{{ event.error }}</span>
            </article>
          </div>
          <pre v-if="agentTraceExpanded" class="trace-json">{{ JSON.stringify(agentResult.trace_events, null, 2) }}</pre>
        </template>

        <template v-else-if="activeResultTab === 'tools'">
          <div v-if="agentToolActions.length === 0" class="empty spacious-empty">模型没有调用任何工具</div>
          <div v-else class="agent-tool-list">
            <article v-for="action in agentToolActions" :key="action.id">
              <header>
                <div>
                  <strong>{{ action.tool_name }}</strong>
                  <span class="risk-badge" :class="action.risk_level">{{ action.risk_level }}</span>
                  <span class="action-status" :class="action.status">{{ toolStatusLabel(action.status) }}</span>
                </div>
                <small>{{ action.permission }} · {{ action.attempt_count }} 次执行</small>
              </header>
              <p>{{ action.reason }}</p>
              <code>{{ action.arguments_summary }}</code>
              <pre v-if="Object.keys(action.result || {}).length">{{ JSON.stringify(action.result, null, 2) }}</pre>
              <p v-if="action.error" class="error">{{ action.error }}</p>
            </article>
          </div>
        </template>

        <template v-else>
          <div v-if="agentSessionMessages.length === 0" class="empty spacious-empty">暂无 Redis 会话消息</div>
          <div v-else class="message-list session-module-list">
            <article v-for="message in agentSessionMessages" :key="message.id || `${message.run_id}-${message.role}`">
              <strong>{{ roleLabel(message.role) }}</strong><span>{{ message.content }}</span>
            </article>
          </div>
        </template>
      </div>
    </section>

    <section v-else class="module-card empty-workspace">
      <span class="empty-illustration">◎</span>
      <h3>尚未运行智能体</h3>
      <p>输入问题后，这里会按模块展示回答、工具决策、记忆、轨迹和会话缓存。</p>
    </section>
  </div>
</template>

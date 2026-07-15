<script setup>
import { onMounted, ref } from 'vue'

import { useWorkspaceContext } from '../composables/workspaceContext'


const {
  approvalActions, approvalStatusFilter, approvalLoading, approvalError,
  approvalDecisionLoading, approvalDetails, pendingApprovalCount,
  fetchToolActions, fetchToolActionDetail, decideToolAction, formatDate
} = useWorkspaceContext()

const expandedActionId = ref('')
const decisionReasons = ref({})

onMounted(() => fetchToolActions(false))

async function toggleDetail(action) {
  if (expandedActionId.value === action.id) {
    expandedActionId.value = ''
    return
  }
  expandedActionId.value = action.id
  if (!approvalDetails.value[action.id]) {
    try {
      await fetchToolActionDetail(action.id)
    } catch {
      // 错误由共享工作区统一展示。
    }
  }
}

async function decide(action, decision) {
  try {
    await decideToolAction(action, decision, decisionReasons.value[action.id] || '')
  } catch {
    // 保留当前输入，便于审批人修正后重试。
  }
}

function riskLabel(risk) {
  return { low: '低风险', medium: '中风险', high: '高风险' }[risk] || risk
}

function actionStatusLabel(status) {
  return {
    proposed: '已提出', pending: '待审批', running: '执行中', executed: '已执行',
    rejected: '已拒绝', failed: '执行失败', blocked: '已阻断'
  }[status] || status
}
</script>

<template>
  <div class="module-page approval-page">
    <section class="module-card approval-hero">
      <div>
        <span class="section-index">Day 6 · Human in the loop</span>
        <h2>工具审批台</h2>
        <p>高风险操作不会由模型直接执行。审批决定具有幂等保护，批准后 Agent 会自动续跑。</p>
      </div>
      <div class="approval-hero-count">
        <strong>{{ pendingApprovalCount }}</strong>
        <span>待处理</span>
      </div>
    </section>

    <section class="module-card approval-workspace">
      <header class="approval-toolbar">
        <label>
          <span>状态</span>
          <select v-model="approvalStatusFilter" @change="fetchToolActions(false)">
            <option value="">全部状态</option>
            <option value="pending">待审批</option>
            <option value="executed">已执行</option>
            <option value="rejected">已拒绝</option>
            <option value="failed">执行失败</option>
            <option value="blocked">已阻断</option>
          </select>
        </label>
        <button class="secondary" :disabled="approvalLoading" @click="fetchToolActions(false)">
          {{ approvalLoading ? '刷新中' : '刷新列表' }}
        </button>
      </header>

      <p v-if="approvalError" class="error approval-error">{{ approvalError }}</p>
      <div v-if="!approvalLoading && approvalActions.length === 0" class="empty spacious-empty">
        当前筛选条件下没有工具 action
      </div>

      <div v-else class="approval-list">
        <article v-for="action in approvalActions" :key="action.id" class="approval-item">
          <header class="approval-item-header">
            <div>
              <span class="risk-badge" :class="action.risk_level">{{ riskLabel(action.risk_level) }}</span>
              <span class="action-status" :class="action.status">{{ actionStatusLabel(action.status) }}</span>
              <h3>{{ action.tool_name }}</h3>
              <p>{{ action.reason }}</p>
            </div>
            <button class="secondary compact-button" @click="toggleDetail(action)">
              {{ expandedActionId === action.id ? '收起详情' : '查看详情' }}
            </button>
          </header>

          <dl class="approval-facts">
            <div><dt>权限</dt><dd>{{ action.permission }}</dd></div>
            <div><dt>发起人</dt><dd>{{ action.requested_by }}</dd></div>
            <div><dt>Run</dt><dd>{{ action.run_id }}</dd></div>
            <div><dt>时间</dt><dd>{{ formatDate(action.created_at) }}</dd></div>
          </dl>

          <div class="argument-summary">
            <span>参数摘要</span>
            <code>{{ action.arguments_summary || JSON.stringify(action.arguments) }}</code>
          </div>

          <div v-if="action.status === 'pending'" class="approval-decision-bar">
            <label>
              <span>审批理由（可选）</span>
              <input v-model="decisionReasons[action.id]" type="text" placeholder="记录批准或拒绝的依据" />
            </label>
            <button
              class="danger"
              :disabled="Boolean(approvalDecisionLoading)"
              @click="decide(action, 'reject')"
            >
              {{ approvalDecisionLoading === `${action.id}:reject` ? '拒绝中' : '拒绝' }}
            </button>
            <button
              class="primary"
              :disabled="Boolean(approvalDecisionLoading)"
              @click="decide(action, 'approve')"
            >
              {{ approvalDecisionLoading === `${action.id}:approve` ? '执行中' : '批准并执行' }}
            </button>
          </div>

          <section v-if="expandedActionId === action.id" class="approval-detail">
            <div class="approval-detail-grid">
              <div>
                <h4>授权证据</h4>
                <p>{{ action.authorization_evidence || '无' }}</p>
                <small>来源：{{ action.authorization_source || '-' }}</small>
              </div>
              <div>
                <h4>执行结果</h4>
                <pre>{{ JSON.stringify(action.result || {}, null, 2) }}</pre>
                <p v-if="action.error" class="error">{{ action.error }}</p>
              </div>
            </div>
            <div class="action-trace">
              <h4>关联 Trace</h4>
              <div v-if="!approvalDetails[action.id]" class="empty">正在加载…</div>
              <ol v-else>
                <li v-for="event in approvalDetails[action.id].trace" :key="event.sequence">
                  <strong>{{ event.sequence }} · {{ event.state }}</strong>
                  <span>{{ event.output_summary }}</span>
                  <small>{{ event.duration_ms }} ms</small>
                </li>
              </ol>
            </div>
          </section>
        </article>
      </div>
    </section>
  </div>
</template>

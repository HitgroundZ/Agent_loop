<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { apiRequest, getJson } from '../services/api'


const metricDefinitions = [
  { key: 'hit_at_k', label: '检索命中率', short: 'Hit@K', color: '#e07a35' },
  { key: 'faithfulness', label: '忠实度', short: 'Faithfulness', color: '#237a66' },
  { key: 'answer_relevancy', label: '回答相关性', short: 'Answer relevance', color: '#3266a8' },
  { key: 'context_precision', label: '上下文精确率', short: 'Context precision', color: '#8062a8' },
  { key: 'context_recall', label: '上下文召回率', short: 'Context recall', color: '#a94f67' }
]

const datasets = ref([])
const runs = ref([])
const selectedDatasetId = ref('')
const selectedStrategy = ref('hybrid')
const selectedTopK = ref(5)
const selectedRun = ref(null)
const selectedCase = ref(null)
const loading = ref(false)
const creating = ref(false)
const error = ref('')
let pollTimer = null

const validDatasets = computed(() => datasets.value.filter((item) => item.valid))
const currentMetrics = computed(() => selectedRun.value?.metrics || {})
const completedRuns = computed(() => runs.value
  .filter((item) => ['completed', 'completed_with_errors'].includes(item.status))
  .slice(0, 12)
  .reverse())
const trendSeries = computed(() => metricDefinitions.map((metric) => ({
  ...metric,
  points: seriesPoints(completedRuns.value.map((run) => run.metrics?.[metric.key]))
})))
const strategyRows = computed(() => ['vector', 'keyword', 'hybrid'].map((strategy) => ({
  strategy,
  run: runs.value.find((item) => item.strategy === strategy && ['completed', 'completed_with_errors'].includes(item.status))
})))
const rankedCases = computed(() => [...(selectedRun.value?.cases || [])].sort(
  (left, right) => (caseScore(left) ?? 2) - (caseScore(right) ?? 2)
))

onMounted(loadInitial)
onUnmounted(stopPolling)

async function loadInitial() {
  loading.value = true
  error.value = ''
  try {
    const [datasetResponse, runResponse] = await Promise.all([
      getJson('/api/evaluations/datasets'),
      getJson('/api/evaluations/runs?limit=50')
    ])
    datasets.value = datasetResponse.items || []
    runs.value = runResponse.items || []
    const preferred = validDatasets.value[0] || datasets.value[0]
    if (preferred) {
      selectedDatasetId.value = preferred.id
      selectedTopK.value = preferred.default_top_k || 5
    }
    if (runs.value[0]) await selectRun(runs.value[0])
  } catch (exc) {
    error.value = exc.message
  } finally {
    loading.value = false
  }
}

async function refreshRuns() {
  const response = await getJson('/api/evaluations/runs?limit=50')
  runs.value = response.items || []
}

async function createRun() {
  if (!selectedDatasetId.value) return
  creating.value = true
  error.value = ''
  try {
    const run = await apiRequest('/api/evaluations/runs', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': globalThis.crypto?.randomUUID?.() || `eval-${Date.now()}`
      },
      body: JSON.stringify({
        dataset_id: selectedDatasetId.value,
        strategy: selectedStrategy.value,
        top_k: Number(selectedTopK.value) || 5
      })
    })
    await refreshRuns()
    await selectRun(run)
  } catch (exc) {
    const details = exc.payload?.detail?.errors
    error.value = details?.length ? `${exc.message}：${details.join('；')}` : exc.message
  } finally {
    creating.value = false
  }
}

async function selectRun(run) {
  stopPolling()
  selectedCase.value = null
  try {
    selectedRun.value = await getJson(`/api/evaluations/runs/${run.id}`)
    if (['queued', 'running'].includes(selectedRun.value.status)) startPolling()
  } catch (exc) {
    error.value = exc.message
  }
}

function startPolling() {
  pollTimer = window.setInterval(async () => {
    if (!selectedRun.value) return
    try {
      const updated = await getJson(`/api/evaluations/runs/${selectedRun.value.id}`)
      selectedRun.value = updated
      await refreshRuns()
      if (!['queued', 'running'].includes(updated.status)) stopPolling()
    } catch (exc) {
      error.value = exc.message
      stopPolling()
    }
  }, 2000)
}

function stopPolling() {
  if (pollTimer) window.clearInterval(pollTimer)
  pollTimer = null
}

function percent(value) {
  return value === null || value === undefined ? '—' : `${(Number(value) * 100).toFixed(1)}%`
}

function coverage(metric) {
  const item = selectedRun.value?.coverage?.[metric]
  if (!item) return '尚无评分'
  return `${item.scored}/${item.total} 已评分`
}

function runStatus(status) {
  return ({
    queued: '排队中', running: '评测中', completed: '已完成',
    completed_with_errors: '部分完成', failed: '失败'
  })[status] || status
}

function strategyLabel(strategy) {
  return ({ vector: '向量检索', keyword: '关键词检索', hybrid: '混合检索' })[strategy] || strategy
}

function seriesPoints(values) {
  if (!values.length) return ''
  const width = 680
  const height = 180
  const left = 34
  const top = 16
  const plotWidth = width - left - 18
  const plotHeight = height - top - 28
  return values.map((value, index) => {
    if (value === null || value === undefined) return null
    const x = left + (values.length === 1 ? plotWidth / 2 : index * plotWidth / (values.length - 1))
    const y = top + (1 - Math.max(0, Math.min(1, Number(value)))) * plotHeight
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).filter(Boolean).join(' ')
}

function caseScore(item) {
  const values = metricDefinitions
    .filter((metric) => metric.key !== 'hit_at_k')
    .map((metric) => item.scores?.[metric.key])
    .filter((value) => value !== null && value !== undefined)
  return values.length ? values.reduce((sum, value) => sum + Number(value), 0) / values.length : null
}

function shortId(value) {
  return value ? value.slice(0, 8) : '—'
}
</script>

<template>
  <div class="module-page evaluation-page">
    <section class="module-card evaluation-launcher">
      <div>
        <span class="section-index">评测任务</span>
        <h2>黄金集量化评测</h2>
        <p>通过真实 Agent 链路检索和回答，再由 Ragas 独立评分。</p>
      </div>
      <div class="evaluation-controls">
        <label><span>数据集</span><select v-model="selectedDatasetId"><option v-for="item in datasets" :key="item.id" :value="item.id" :disabled="!item.valid">{{ item.name }} · {{ item.version }}{{ item.valid ? '' : '（不可用）' }}</option></select></label>
        <label><span>检索策略</span><select v-model="selectedStrategy"><option value="hybrid">混合检索</option><option value="vector">向量检索</option><option value="keyword">关键词检索</option></select></label>
        <label><span>Top K</span><input v-model="selectedTopK" type="number" min="1" max="50" /></label>
        <button class="primary" :disabled="creating || !validDatasets.length" @click="createRun">{{ creating ? '正在提交' : '开始评测' }}</button>
      </div>
      <p v-if="error" class="error evaluation-error">{{ error }}</p>
      <div v-if="datasets.some((item) => !item.valid)" class="dataset-warning">
        <strong>数据集校验提示</strong>
        <p v-for="item in datasets.filter((entry) => !entry.valid)" :key="item.id">{{ item.name }}：{{ item.validation_errors.join('；') }}</p>
      </div>
    </section>

    <section v-if="selectedRun" class="evaluation-run-banner module-card">
      <div><span class="section-index">当前批次</span><h3>{{ selectedRun.dataset_id }} · {{ selectedRun.dataset_version }}</h3><p>{{ strategyLabel(selectedRun.strategy) }} / Hit@{{ selectedRun.top_k }} / Judge {{ selectedRun.judge_model }}</p></div>
      <div class="evaluation-progress-block"><span class="status" :class="selectedRun.status">{{ runStatus(selectedRun.status) }}</span><strong>{{ selectedRun.completed_cases }}/{{ selectedRun.total_cases }}</strong><div class="evaluation-progress"><i :style="{ width: `${selectedRun.progress * 100}%` }"></i></div></div>
    </section>

    <section class="evaluation-kpis">
      <article v-for="metric in metricDefinitions" :key="metric.key" class="module-card" :style="{ '--metric-color': metric.color }">
        <span>{{ metric.label }}</span>
        <strong>{{ percent(currentMetrics[metric.key]) }}</strong>
        <small>{{ metric.key === 'hit_at_k' && selectedRun ? `Hit@${selectedRun.top_k} · ${selectedRun.metrics?.hit_at_k !== undefined ? Math.round(selectedRun.metrics.hit_at_k * selectedRun.total_cases) : 0}/${selectedRun.total_cases}` : coverage(metric.key) }}</small>
      </article>
    </section>

    <section class="evaluation-visual-grid">
      <article class="module-card evaluation-chart-card">
        <header><div><span class="section-index">历史趋势</span><h3>最近批次指标</h3></div><small>{{ completedRuns.length }} 个已完成批次</small></header>
        <div v-if="completedRuns.length" class="trend-chart">
          <svg viewBox="0 0 680 180" role="img" aria-label="评测指标历史趋势">
            <line v-for="level in [0, .25, .5, .75, 1]" :key="level" x1="34" x2="662" :y1="16 + (1-level)*136" :y2="16 + (1-level)*136" class="chart-grid-line" />
            <text v-for="level in [0, .5, 1]" :key="`label-${level}`" x="4" :y="20 + (1-level)*136" class="chart-axis-label">{{ Math.round(level*100) }}</text>
            <polyline v-for="series in trendSeries" :key="series.key" :points="series.points" :stroke="series.color" class="trend-line" />
          </svg>
          <div class="chart-legend"><span v-for="metric in metricDefinitions" :key="metric.key"><i :style="{ background: metric.color }"></i>{{ metric.label }}</span></div>
        </div>
        <p v-else class="empty">完成一次评测后展示趋势</p>
      </article>

      <article class="module-card evaluation-chart-card">
        <header><div><span class="section-index">策略对比</span><h3>各策略最近成绩</h3></div></header>
        <div class="strategy-chart">
          <div v-for="row in strategyRows" :key="row.strategy" class="strategy-row">
            <strong>{{ strategyLabel(row.strategy) }}</strong>
            <div class="strategy-bars">
              <span v-for="metric in metricDefinitions" :key="metric.key" :title="`${metric.label} ${percent(row.run?.metrics?.[metric.key])}`"><i :style="{ width: `${(row.run?.metrics?.[metric.key] || 0) * 100}%`, background: metric.color }"></i></span>
            </div>
            <small>{{ row.run ? `#${shortId(row.run.id)}` : '暂无' }}</small>
          </div>
        </div>
        <div class="chart-legend compact"><span v-for="metric in metricDefinitions" :key="metric.key"><i :style="{ background: metric.color }"></i>{{ metric.label }}</span></div>
      </article>
    </section>

    <section class="evaluation-detail-grid">
      <article class="module-card evaluation-history">
        <header><div><span class="section-index">历史批次</span><h3>可复现评测记录</h3></div><button class="secondary small" @click="refreshRuns">刷新</button></header>
        <button v-for="run in runs" :key="run.id" :class="{ active: selectedRun?.id === run.id }" @click="selectRun(run)">
          <span><strong>{{ run.dataset_id }} · {{ strategyLabel(run.strategy) }}</strong><small>{{ new Date(run.created_at).toLocaleString() }}</small></span>
          <span><b>{{ percent(run.metrics?.faithfulness) }}</b><small>{{ runStatus(run.status) }}</small></span>
        </button>
        <p v-if="!runs.length && !loading" class="empty">暂无评测记录</p>
      </article>

      <article class="module-card evaluation-cases">
        <header><div><span class="section-index">案例诊断</span><h3>低分案例优先</h3></div><small>{{ rankedCases.length }} 个案例</small></header>
        <div class="evaluation-case-table">
          <button v-for="item in rankedCases" :key="item.case_id" :class="{ active: selectedCase?.case_id === item.case_id }" @click="selectedCase = item">
            <span><strong>{{ item.question }}</strong><small>{{ item.case_id }} · {{ item.status }}</small></span>
            <span class="case-score"><b>{{ percent(caseScore(item)) }}</b><small>{{ item.hit_at_k ? `命中@${selectedRun.top_k}` : `未命中@${selectedRun.top_k}` }}</small></span>
          </button>
          <p v-if="!rankedCases.length" class="empty">批次完成后可查看逐案例评分</p>
        </div>
      </article>
    </section>

    <section v-if="selectedCase" class="module-card case-inspector">
      <header><div><span class="section-index">案例详情</span><h3>{{ selectedCase.question }}</h3></div><button class="secondary small" @click="selectedCase = null">关闭</button></header>
      <div class="case-answer-grid"><article><span>标准答案</span><p>{{ selectedCase.reference_answer }}</p></article><article><span>实际答案</span><p>{{ selectedCase.answer || '无回答' }}</p></article></div>
      <div class="case-metric-reasons">
        <article v-for="metric in metricDefinitions.filter((item) => item.key !== 'hit_at_k')" :key="metric.key"><header><strong>{{ metric.label }}</strong><b>{{ percent(selectedCase.scores?.[metric.key]) }}</b></header><p>{{ selectedCase.reasons?.[metric.key] || '无评分说明' }}</p></article>
      </div>
      <div class="case-context-grid">
        <article><h4>黄金上下文</h4><div v-for="context in selectedCase.reference_contexts" :key="context.context_id"><strong>{{ context.document_name }}</strong><small>{{ context.context_id }}</small><p>{{ context.text }}</p></div></article>
        <article><h4>实际检索上下文</h4><div v-for="context in selectedCase.retrieved_contexts" :key="context.context_id || context.chunk_id"><strong>#{{ context.rank }} {{ context.document_name }}</strong><small>{{ context.context_id || '无稳定 ID' }}</small><p>{{ context.text }}</p></div><p v-if="!selectedCase.retrieved_contexts.length" class="empty">未检索到上下文</p></article>
      </div>
    </section>
  </div>
</template>

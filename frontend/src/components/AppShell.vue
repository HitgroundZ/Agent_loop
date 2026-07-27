<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { provideWorkspaceContext } from '../composables/workspaceContext'
import AgentView from '../views/AgentView.vue'
import ApprovalView from '../views/ApprovalView.vue'
import EvaluationView from '../views/EvaluationView.vue'
import KnowledgeView from '../views/KnowledgeView.vue'
import MemoryView from '../views/MemoryView.vue'
import OverviewView from '../views/OverviewView.vue'
import RetrievalView from '../views/RetrievalView.vue'


const workspace = provideWorkspaceContext()
const modules = [
  { id: 'overview', label: '总览', shortLabel: '总览', glyph: '⌂', component: OverviewView, description: '系统状态与工作入口' },
  { id: 'agent', label: '智能体', shortLabel: 'Agent', glyph: '◎', component: AgentView, description: '运行问答、查看引用与执行轨迹' },
  { id: 'approval', label: '审批台', shortLabel: '审批', glyph: '✓', component: ApprovalView, description: '审查高风险工具调用并安全续跑' },
  { id: 'memory', label: '长期记忆', shortLabel: '记忆', glyph: '◇', component: MemoryView, description: '查看、禁用、纠错与追溯用户记忆' },
  { id: 'knowledge', label: '知识库', shortLabel: '文档', glyph: '▤', component: KnowledgeView, description: '上传文档并管理切片和向量状态' },
  { id: 'retrieval', label: '检索实验室', shortLabel: '检索', glyph: '⌕', component: RetrievalView, description: '测试向量、关键词与混合检索' },
  { id: 'evaluation', label: '评测中心', shortLabel: '评测', glyph: '◒', component: EvaluationView, description: '量化命中率、忠实度与上下文质量' }
]

const currentModuleId = ref(readModuleFromHash())
const currentModule = computed(() => modules.find((item) => item.id === currentModuleId.value) || modules[0])

onMounted(() => window.addEventListener('hashchange', syncHash))
onUnmounted(() => window.removeEventListener('hashchange', syncHash))

function readModuleFromHash() {
  const requested = window.location.hash.replace(/^#\/?/, '')
  return modules.some((item) => item.id === requested) ? requested : 'overview'
}

function syncHash() {
  currentModuleId.value = readModuleFromHash()
}

function navigate(moduleId) {
  if (currentModuleId.value === moduleId) return
  window.location.hash = moduleId
}
</script>

<template>
  <div class="app-shell">
    <aside class="app-navigation">
      <button class="app-brand" aria-label="返回总览" @click="navigate('overview')">
        <span class="brand-mark">AL</span>
        <span class="brand-copy">
          <strong>Agent Loop</strong>
          <small>Knowledge OS</small>
        </span>
      </button>

      <nav class="module-navigation" aria-label="主模块导航">
        <button
          v-for="item in modules"
          :key="item.id"
          class="module-nav-item"
          :class="{ active: currentModuleId === item.id }"
          :aria-current="currentModuleId === item.id ? 'page' : undefined"
          @click="navigate(item.id)"
        >
          <span class="nav-glyph">{{ item.glyph }}</span>
          <span class="nav-label">{{ item.label }}</span>
          <span v-if="item.id === 'approval' && workspace.pendingApprovalCount.value" class="nav-count">
            {{ workspace.pendingApprovalCount.value }}
          </span>
        </button>
      </nav>

      <div class="navigation-footer">
        <span class="service-indicator" :class="workspace.health.value">
          <i></i>{{ workspace.health.value === 'ok' ? '服务正常' : '服务异常' }}
        </span>
        <small>Day 10 · Integrated</small>
      </div>
    </aside>

    <section class="app-stage">
      <header class="module-topbar">
        <div class="module-heading">
          <span class="module-kicker">{{ currentModule.shortLabel }}</span>
          <div>
            <h1>{{ currentModule.label }}</h1>
            <p>{{ currentModule.description }}</p>
          </div>
        </div>
        <label class="global-user-control">
          <span>当前用户</span>
          <input v-model="workspace.agentUserId.value" type="text" placeholder="demo-user" />
        </label>
      </header>

      <main class="module-viewport">
        <component :is="currentModule.component" @navigate="navigate" />
      </main>

      <nav class="mobile-module-navigation" aria-label="移动端模块导航">
        <button
          v-for="item in modules"
          :key="item.id"
          :class="{ active: currentModuleId === item.id }"
          @click="navigate(item.id)"
        >
          <span>{{ item.glyph }}</span>
          <small>{{ item.shortLabel }}</small>
          <i v-if="item.id === 'approval' && workspace.pendingApprovalCount.value" class="mobile-nav-count">
            {{ workspace.pendingApprovalCount.value }}
          </i>
        </button>
      </nav>
    </section>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

import { deleteJson, getJson, patchJson, postJson } from '../services/api'


const props = defineProps({
  userId: { type: String, default: 'demo-user' }
})

const memories = ref([])
const messages = ref([])
const loading = ref(false)
const errorMessage = ref('')
const categoryFilter = ref('all')
const enabledFilter = ref('all')
const activeView = ref('memories')
const correctionId = ref(null)
const correctionContent = ref('')
const correctionReason = ref('')

const filteredMemories = computed(() => memories.value.filter((memory) => {
  if (categoryFilter.value !== 'all' && memory.category !== categoryFilter.value) return false
  if (enabledFilter.value === 'enabled' && !memory.enabled) return false
  if (enabledFilter.value === 'disabled' && memory.enabled) return false
  return true
}))

watch(
  () => props.userId,
  (userId) => {
    if (userId?.trim()) refresh()
  },
  { immediate: true }
)

async function refresh() {
  const userId = props.userId.trim()
  if (!userId) return
  loading.value = true
  errorMessage.value = ''
  try {
    const encodedUser = encodeURIComponent(userId)
    const [memoryData, messageData] = await Promise.all([
      getJson(`/api/memories?user_id=${encodedUser}&limit=200`),
      getJson(`/api/memories/messages?user_id=${encodedUser}&limit=200`)
    ])
    memories.value = memoryData.items || []
    messages.value = messageData.items || []
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    loading.value = false
  }
}

async function toggleMemory(memory) {
  errorMessage.value = ''
  try {
    const updated = await patchJson(`/api/memories/${memory.id}`, { enabled: !memory.enabled })
    memories.value = memories.value.map((item) => item.id === memory.id ? updated : item)
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function removeMemory(memory) {
  if (!window.confirm('确定永久删除这条长期记忆吗？原始对话不会被删除。')) return
  errorMessage.value = ''
  try {
    await deleteJson(`/api/memories/${memory.id}`)
    memories.value = memories.value.filter((item) => item.id !== memory.id)
    if (correctionId.value === memory.id) cancelCorrection()
  } catch (error) {
    errorMessage.value = error.message
  }
}

function beginCorrection(memory) {
  correctionId.value = memory.id
  correctionContent.value = memory.content
  correctionReason.value = ''
}

function cancelCorrection() {
  correctionId.value = null
  correctionContent.value = ''
  correctionReason.value = ''
}

async function submitCorrection(memory) {
  if (!correctionContent.value.trim()) {
    errorMessage.value = '纠错内容不能为空'
    return
  }
  errorMessage.value = ''
  try {
    const correction = await postJson(`/api/memories/${memory.id}/corrections`, {
      corrected_content: correctionContent.value.trim(),
      reason: correctionReason.value.trim() || null
    })
    memories.value = [
      correction,
      ...memories.value.map((item) => item.id === memory.id ? { ...item, enabled: false } : item)
    ]
    cancelCorrection()
  } catch (error) {
    errorMessage.value = error.message
  }
}

function categoryLabel(category) {
  return {
    event_summary: '事件摘要',
    scene: '场景记忆',
    user_profile: '用户画像',
    human_correction: '人工纠错'
  }[category] || category
}

function roleLabel(role) {
  return { user: '用户', assistant: '助手' }[role] || role
}

function formatDate(value) {
  if (!value) return '-'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
  }).format(new Date(value))
}
</script>

<template>
  <section class="panel memory-panel">
    <header class="memory-header">
      <div>
        <p class="eyebrow">第 5 天</p>
        <h2>长期记忆管理</h2>
        <p class="muted">用户 {{ userId || '-' }} · 禁用的记忆不会进入后续 prompt</p>
      </div>
      <div class="memory-header-actions">
        <button class="secondary" :class="{ active: activeView === 'memories' }" @click="activeView = 'memories'">
          记忆 {{ memories.length }}
        </button>
        <button class="secondary" :class="{ active: activeView === 'messages' }" @click="activeView = 'messages'">
          原始对话 {{ messages.length }}
        </button>
        <button class="secondary" :disabled="loading || !userId" @click="refresh">
          {{ loading ? '刷新中' : '刷新' }}
        </button>
      </div>
    </header>

    <p v-if="errorMessage" class="error">{{ errorMessage }}</p>

    <template v-if="activeView === 'memories'">
      <div class="memory-filters">
        <label>
          <span>类型</span>
          <select v-model="categoryFilter">
            <option value="all">全部</option>
            <option value="event_summary">事件摘要</option>
            <option value="scene">场景记忆</option>
            <option value="user_profile">用户画像</option>
            <option value="human_correction">人工纠错</option>
          </select>
        </label>
        <label>
          <span>状态</span>
          <select v-model="enabledFilter">
            <option value="all">全部</option>
            <option value="enabled">已启用</option>
            <option value="disabled">已禁用</option>
          </select>
        </label>
      </div>

      <div v-if="!loading && filteredMemories.length === 0" class="empty">该用户暂无匹配记忆</div>
      <div v-else class="memory-list">
        <article v-for="memory in filteredMemories" :key="memory.id" class="memory-row" :class="{ disabled: !memory.enabled }">
          <header>
            <div class="memory-tags">
              <span class="status" :class="memory.enabled ? 'indexed' : 'failed'">
                {{ memory.enabled ? '已启用' : '已禁用' }}
              </span>
              <strong>{{ categoryLabel(memory.category) }}</strong>
              <span>{{ formatDate(memory.created_at) }}</span>
              <span>召回 {{ memory.access_count || 0 }} 次</span>
            </div>
            <div class="memory-actions">
              <button class="secondary compact-button" @click="toggleMemory(memory)">
                {{ memory.enabled ? '禁用' : '启用' }}
              </button>
              <button class="secondary compact-button" @click="beginCorrection(memory)">纠错</button>
              <button class="danger compact-button" @click="removeMemory(memory)">删除</button>
            </div>
          </header>
          <p class="memory-content">{{ memory.content }}</p>
          <div class="memory-source">
            <span>记忆 ID {{ memory.id }}</span>
            <span v-if="memory.source_message_id">来源消息 {{ memory.source_message_id }}</span>
            <span v-if="memory.source_document_id">来源文档 {{ memory.source_document_id }}</span>
            <span v-if="memory.parent_memory_id">纠正记忆 {{ memory.parent_memory_id }}</span>
          </div>
          <details v-if="memory.source_message">
            <summary>查看原始消息</summary>
            <p>{{ memory.source_message.content }}</p>
          </details>

          <form v-if="correctionId === memory.id" class="correction-form" @submit.prevent="submitCorrection(memory)">
            <label>
              <span>纠正后的内容</span>
              <textarea v-model="correctionContent" rows="3"></textarea>
            </label>
            <label>
              <span>纠错原因（可选）</span>
              <input v-model="correctionReason" type="text" placeholder="说明人工判断依据" />
            </label>
            <div class="memory-actions">
              <button type="button" class="secondary" @click="cancelCorrection">取消</button>
              <button type="submit" class="primary">保存纠错</button>
            </div>
          </form>
        </article>
      </div>
    </template>

    <template v-else>
      <div v-if="!loading && messages.length === 0" class="empty">该用户暂无原始对话</div>
      <div v-else class="message-list raw-message-list">
        <article v-for="message in messages" :key="message.id">
          <strong>{{ roleLabel(message.role) }}</strong>
          <span>{{ message.content }}</span>
          <small>{{ message.session_id }} · {{ message.id }} · {{ formatDate(message.created_at) }}</small>
        </article>
      </div>
    </template>
  </section>
</template>

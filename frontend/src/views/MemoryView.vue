<script setup>
import MemoryManager from '../components/MemoryManager.vue'
import { useWorkspaceContext } from '../composables/workspaceContext'


const { agentUserId } = useWorkspaceContext()
</script>

<template>
  <div class="module-page memory-page">
    <section class="memory-intro">
      <div>
        <span class="section-index">记忆治理</span>
        <h2>只让可信、相关的记忆进入上下文</h2>
        <p>当前正在管理用户 <strong>{{ agentUserId }}</strong>。切换右上角用户 ID 即可查看其他用户。</p>
      </div>
      <div class="memory-policy">
        <span>召回规则</span>
        <strong>启用 + 相关 + 可追溯</strong>
      </div>
    </section>
    <MemoryManager :user-id="agentUserId" />
  </div>
</template>

import { inject, provide } from 'vue'

import { useKnowledgeWorkspace } from './useKnowledgeWorkspace'


const WORKSPACE_KEY = Symbol('agent-loop-workspace')

export function provideWorkspaceContext() {
  const workspace = useKnowledgeWorkspace()
  provide(WORKSPACE_KEY, workspace)
  return workspace
}

export function useWorkspaceContext() {
  const workspace = inject(WORKSPACE_KEY)
  if (!workspace) {
    throw new Error('Workspace context is not available')
  }
  return workspace
}

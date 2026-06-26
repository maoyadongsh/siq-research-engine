export type AgentAvatarKind = 'analysis' | 'factchecker' | 'tracking' | 'legal'
export type AgentAvatarState = 'idle' | 'thinking' | 'replying' | 'error'

export function agentKindFromApiPrefix(apiPrefix: string): AgentAvatarKind {
  if (apiPrefix.includes('factchecker')) return 'factchecker'
  if (apiPrefix.includes('tracking')) return 'tracking'
  if (apiPrefix.includes('legal')) return 'legal'
  return 'analysis'
}

export function agentChatAuthKey() {
  if (typeof window === 'undefined') return 'anonymous'
  try {
    const savedUser = window.localStorage.getItem('user')
    if (savedUser) {
      const parsed = JSON.parse(savedUser) as { id?: unknown; username?: unknown }
      if (parsed?.id != null) return `user:${String(parsed.id)}`
      if (parsed?.username) return `username:${String(parsed.username)}`
    }
  } catch {
    // Ignore malformed auth cache and fall back to the token fingerprint.
  }
  const token = window.localStorage.getItem('access_token') || ''
  return token ? `token:${token.slice(0, 16)}` : 'anonymous'
}

const DEFAULT_MAX_ENTRIES = 2
const DEFAULT_MAX_CHARS = 2_000_000

export class ArtifactContentCache {
  private readonly entries = new Map<string, string>()
  private totalChars = 0
  private readonly maxEntries: number
  private readonly maxChars: number

  constructor(maxEntries = DEFAULT_MAX_ENTRIES, maxChars = DEFAULT_MAX_CHARS) {
    this.maxEntries = maxEntries
    this.maxChars = maxChars
  }

  get(artifactId: string) {
    const value = this.entries.get(artifactId)
    if (value === undefined) return undefined
    this.entries.delete(artifactId)
    this.entries.set(artifactId, value)
    return value
  }

  set(artifactId: string, html: string) {
    this.delete(artifactId)
    if (!artifactId || html.length > this.maxChars || this.maxEntries < 1) return
    this.entries.set(artifactId, html)
    this.totalChars += html.length
    while (this.entries.size > this.maxEntries || this.totalChars > this.maxChars) {
      const oldest = this.entries.entries().next().value as [string, string] | undefined
      if (!oldest) break
      this.entries.delete(oldest[0])
      this.totalChars -= oldest[1].length
    }
  }

  delete(artifactId: string) {
    const value = this.entries.get(artifactId)
    if (value === undefined) return
    this.entries.delete(artifactId)
    this.totalChars -= value.length
  }
}

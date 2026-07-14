import { defaultRangeExtractor, type Range } from '@tanstack/react-virtual'

export const TRANSCRIPT_OVERSCAN = 6
export const TRANSCRIPT_MAX_RENDERED_SEGMENTS = 40
export const TRANSCRIPT_ESTIMATED_SEGMENT_HEIGHT = 112

export function transcriptRangeExtractor(range: Range, pinnedIndex = -1) {
  const defaultIndexes = defaultRangeExtractor(range)
  const pinnedOutsideRange = pinnedIndex >= 0 && !defaultIndexes.includes(pinnedIndex)
  const baseBudget = TRANSCRIPT_MAX_RENDERED_SEGMENTS - (pinnedOutsideRange ? 1 : 0)

  let indexes = defaultIndexes
  if (defaultIndexes.length > baseBudget) {
    const visibleCount = range.endIndex - range.startIndex + 1
    const extraBudget = Math.max(0, baseBudget - visibleCount)
    let start = Math.max(0, range.startIndex - Math.floor(extraBudget / 2))
    const end = Math.min(range.count - 1, start + baseBudget - 1)
    start = Math.max(0, end - baseBudget + 1)
    indexes = Array.from({ length: Math.max(0, end - start + 1) }, (_, offset) => start + offset)
  }

  if (pinnedIndex >= 0 && pinnedIndex < range.count && !indexes.includes(pinnedIndex)) {
    indexes = [...indexes, pinnedIndex].sort((left, right) => left - right)
  }
  return indexes
}

export const PDF_MARKDOWN_VIRTUAL_LINE_THRESHOLD = 900
export const PDF_MARKDOWN_VIRTUAL_LONG_LINE_LIMIT = 260

export function hasWrappedLongMarkdownLines(lines: string[], limit = PDF_MARKDOWN_VIRTUAL_LONG_LINE_LIMIT) {
  return lines.some((line) => line.length > limit)
}

export function shouldWindowPdfMarkdownLines(
  lines: string[],
  threshold = PDF_MARKDOWN_VIRTUAL_LINE_THRESHOLD,
  longLineLimit = PDF_MARKDOWN_VIRTUAL_LONG_LINE_LIMIT,
) {
  return lines.length > threshold && !hasWrappedLongMarkdownLines(lines, longLineLimit)
}

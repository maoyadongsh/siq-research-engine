export const MARKET_PARSING_MAX_UPLOAD_FILES = 5
export const MARKET_PARSING_MAX_UPLOAD_FILE_BYTES = 100 * 1024 * 1024

export interface MarketParsingUploadValidationResult {
  files: File[]
  error: string | null
}

export function validateMarketParsingUploadFiles(files: FileList | File[]): MarketParsingUploadValidationResult {
  const incoming = Array.from(files)
  if (!incoming.length) return { files: [], error: null }

  if (incoming.length > MARKET_PARSING_MAX_UPLOAD_FILES) {
    return { files: [], error: '一次最多选择 5 个 PDF' }
  }

  for (const file of incoming) {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      return { files: [], error: '仅支持 PDF 文件' }
    }
    if (file.size > MARKET_PARSING_MAX_UPLOAD_FILE_BYTES) {
      return { files: [], error: `文件超过 100 MB: ${file.name}` }
    }
  }

  return { files: incoming, error: null }
}

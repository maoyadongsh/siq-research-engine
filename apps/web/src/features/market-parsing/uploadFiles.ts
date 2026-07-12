export const MARKET_PARSING_MAX_UPLOAD_FILES = 5
export const MARKET_PARSING_MAX_UPLOAD_FILE_BYTES = 100 * 1024 * 1024
export const MARKET_PARSING_MAX_UPLOAD_BATCH_BYTES = 200 * 1024 * 1024

const PDF_UPLOAD_SUFFIXES = new Set(['.pdf'])
const US_SEC_UPLOAD_SUFFIXES = new Set(['.pdf', '.html', '.htm', '.xhtml', '.xml', '.xbrl', '.zip'])

export interface MarketParsingUploadValidationResult {
  files: File[]
  error: string | null
}

interface UploadValidationPolicy {
  allowedSuffixes: ReadonlySet<string>
  countError: string
  suffixError: (file: File) => string
}

function validateUploadFiles(
  files: FileList | File[],
  policy: UploadValidationPolicy,
): MarketParsingUploadValidationResult {
  const incoming = Array.from(files)
  if (!incoming.length) return { files: [], error: null }

  if (incoming.length > MARKET_PARSING_MAX_UPLOAD_FILES) {
    return { files: [], error: policy.countError }
  }

  let batchBytes = 0
  for (const file of incoming) {
    const lowerName = file.name.toLowerCase()
    const suffixStart = lowerName.lastIndexOf('.')
    const suffix = suffixStart >= 0 ? lowerName.slice(suffixStart) : ''
    if (!policy.allowedSuffixes.has(suffix)) {
      return { files: [], error: policy.suffixError(file) }
    }
    if (file.size === 0) {
      return { files: [], error: `文件不能为空: ${file.name}` }
    }
    if (file.size > MARKET_PARSING_MAX_UPLOAD_FILE_BYTES) {
      return { files: [], error: `文件超过 100 MB: ${file.name}` }
    }
    batchBytes += file.size
  }

  if (batchBytes > MARKET_PARSING_MAX_UPLOAD_BATCH_BYTES) {
    return { files: [], error: '文件总大小超过 200 MB' }
  }

  return { files: incoming, error: null }
}

export function validateMarketParsingUploadFiles(files: FileList | File[]): MarketParsingUploadValidationResult {
  return validateUploadFiles(files, {
    allowedSuffixes: PDF_UPLOAD_SUFFIXES,
    countError: '一次最多选择 5 个 PDF',
    suffixError: () => '仅支持 PDF 文件',
  })
}

export function validateUsSecUploadFiles(files: FileList | File[]): MarketParsingUploadValidationResult {
  return validateUploadFiles(files, {
    allowedSuffixes: US_SEC_UPLOAD_SUFFIXES,
    countError: '一次最多选择 5 个文件',
    suffixError: (file) => `仅支持 PDF / HTML / XHTML / XML / XBRL / ZIP 文件: ${file.name}`,
  })
}

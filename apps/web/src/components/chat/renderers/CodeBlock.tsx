import { useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { copyText } from '@/lib/clipboard'
import { MarkdownBlocks } from './MarkdownBlocks'
import { renderSafeHtmlFragment } from './HtmlTableRenderer'
import {
  hasMarkdownTable,
  hasHtmlTable,
  hasRuntimeCitationLines,
  isMarkdownCodeLanguage,
} from './rendererUtils'

export interface CodeBlockProps {
  language: string
  code: string
  blockIndex: number
  streaming?: boolean
}

export function CodeBlock({ language, code, blockIndex }: CodeBlockProps) {
  const [copiedCode, setCopiedCode] = useState<string | null>(null)
  const codeLanguage = language.trim()

  if (hasRuntimeCitationLines(code) && (isMarkdownCodeLanguage(codeLanguage) || codeLanguage === 'text')) {
    return (
      <MarkdownBlocks
        lines={code.replace(/\r\n?/g, '\n').split('\n')}
        streaming={false}
      />
    )
  }

  if (hasMarkdownTable(code) && (isMarkdownCodeLanguage(codeLanguage) || codeLanguage === 'text')) {
    return (
      <MarkdownBlocks
        lines={code.replace(/\r\n?/g, '\n').split('\n')}
        streaming={false}
      />
    )
  }

  if (hasHtmlTable(code) && (/^(?:html?|xml|text)$/i.test(codeLanguage) || !codeLanguage)) {
    const htmlElements = renderSafeHtmlFragment(code, `html-code-${blockIndex}`)
    if (htmlElements) {
      return <div className="chat-html-fragment">{htmlElements}</div>
    }
  }

  const copyCode = async () => {
    if (await copyText(code)) {
      setCopiedCode(String(blockIndex))
      window.setTimeout(() => setCopiedCode(null), 1400)
    } else {
      setCopiedCode(null)
    }
  }

  const copied = copiedCode === String(blockIndex)

  return (
    <div className="chat-code-block">
      <div className="chat-code-toolbar">
        <span>{language}</span>
        <button type="button" onClick={copyCode} aria-label="复制代码">
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  )
}

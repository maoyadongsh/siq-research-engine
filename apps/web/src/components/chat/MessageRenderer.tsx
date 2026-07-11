import { CodeBlock } from './renderers/CodeBlock'
import { MarkdownBlocks } from './renderers/MarkdownBlocks'
import { splitFencedCode, type MessageRendererProps } from './renderers/rendererUtils'

export default function MessageRenderer({ content, streaming = false, variant = 'assistant', auditTraceApiPrefix = '/api' }: MessageRendererProps) {
  const blocks = splitFencedCode(content)

  return (
    <div className={`chat-rendered chat-rendered-${variant}`}>
      {blocks.map((block, index) => {
        if (block.type === 'code') {
          return (
            <CodeBlock
              key={index}
              language={block.language}
              code={block.code}
              blockIndex={index}
              streaming={streaming}
            />
          )
        }

        return <MarkdownBlocks key={index} lines={block.lines} streaming={streaming} auditTraceApiPrefix={auditTraceApiPrefix} />
      })}
    </div>
  )
}

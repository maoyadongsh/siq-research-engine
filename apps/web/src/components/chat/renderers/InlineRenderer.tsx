import { type MouseEvent as ReactMouseEvent, type ReactNode } from 'react'
import {
  isSafeLinkHref,
  normalizeLinkHref,
  isLikelyInlineMathExpression,
  normalizeInlineMathExpression,
} from './rendererUtils'
import { handleAuthenticatedSourceClick } from '@/lib/authenticatedSourceLinks'

function sourceLinkClickHandler(href: string) {
  return (event: ReactMouseEvent<HTMLAnchorElement>) => {
    handleAuthenticatedSourceClick(event.nativeEvent, href).catch((error) => {
      console.warn('Failed to open authenticated source link', error)
    })
  }
}

export function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let remaining = text
  let index = 0

  const patterns = [
    { type: 'code', regex: /`([^`]+)`/ },
    { type: 'image', regex: /!\[([^\]]*)\]\((https?:\/\/[^)\s]+|\/[^)\s]*|#[^)\s]*)\)/ },
    { type: 'link', regex: /\[([^\]]+)\]\((https?:\/\/[^)\s]+|\/[^)\s]*|#[^)\s]*)\)/ },
    { type: 'math', regex: /\$(?!\$)([^$\n]{1,160})\$(?!\$)/ },
    { type: 'bold', regex: /\*\*([^*]+)\*\*/ },
    { type: 'italic', regex: /(^|[^*])\*([^*\n]+)\*/ },
    { type: 'url', regex: /https?:\/\/[^\s)]+/ },
    { type: 'api-url', regex: /\/api\/[^\s)，）]+/ },
  ]

  while (remaining) {
    let earliest: { type: string; match: RegExpExecArray } | null = null

    for (const pattern of patterns) {
      const match = pattern.regex.exec(remaining)
      if (!match) continue
      if (!earliest || match.index < earliest.match.index) {
        earliest = { type: pattern.type, match }
      }
    }

    if (!earliest) {
      nodes.push(remaining)
      break
    }

    const { type, match } = earliest
    if (match.index > 0) nodes.push(remaining.slice(0, match.index))

    const key = `${keyPrefix}-${index}`
    if (type === 'code') {
      nodes.push(<code key={key} className="chat-inline-code">{match[1]}</code>)
    } else if (type === 'image') {
      const href = match[2]
      const alt = match[1] || '图片附件'
      nodes.push(isSafeLinkHref(href) ? (
        <a key={key} href={normalizeLinkHref(href)} target="_blank" rel="noreferrer" className="chat-image-link">
          <img
            src={normalizeLinkHref(href)}
            alt={alt}
            width={288}
            height={224}
            className="chat-inline-image"
            loading="lazy"
            decoding="async"
          />
        </a>
      ) : match[0])
    } else if (type === 'link') {
      const href = match[2]
      nodes.push(isSafeLinkHref(href) ? (
        <a key={key} href={normalizeLinkHref(href)} target="_blank" rel="noreferrer" className="chat-link" onClick={sourceLinkClickHandler(href)}>
          {match[1]}
        </a>
      ) : match[0])
    } else if (type === 'math') {
      nodes.push(isLikelyInlineMathExpression(match[1]) ? normalizeInlineMathExpression(match[1]) : match[0])
    } else if (type === 'bold') {
      nodes.push(<strong key={key}>{match[1]}</strong>)
    } else if (type === 'italic') {
      nodes.push(
        <span key={key}>
          {match[1]}
          <em>{match[2]}</em>
        </span>,
      )
    } else if (type === 'url') {
      nodes.push(
        <a key={key} href={normalizeLinkHref(match[0])} target="_blank" rel="noreferrer" className="chat-link" onClick={sourceLinkClickHandler(match[0])}>
          {match[0]}
        </a>,
      )
    } else if (type === 'api-url') {
      nodes.push(
        <a key={key} href={normalizeLinkHref(match[0])} target="_blank" rel="noreferrer" className="chat-link" onClick={sourceLinkClickHandler(match[0])}>
          {match[0]}
        </a>,
      )
    }

    remaining = remaining.slice(match.index + match[0].length)
    index += 1
  }

  return nodes
}

export function renderTextWithBreaks(text: string, keyPrefix: string) {
  return text.split('\n').map((line, index, arr) => (
    <span key={`${keyPrefix}-${index}`}>
      {renderInline(line, `${keyPrefix}-${index}`)}
      {index < arr.length - 1 && <br />}
    </span>
  ))
}

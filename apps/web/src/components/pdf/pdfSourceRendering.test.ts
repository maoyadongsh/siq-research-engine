/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import type { PageContent } from '../../lib/pdfTypes.ts'

const { renderPageContentHtml } = await import('./pdfSourceRendering.ts')

test('renderPageContentHtml includes table and image footnotes in page mode', () => {
  const page = {
    page_number: 80,
    block_count: 2,
    table_count: 1,
    page_tables: [{ table_index: 17, matched_financial_names: ['取締役会'] }],
    blocks: [
      {
        block_id: 'b001016',
        type: 'table',
        bbox: [120, 150, 820, 760],
        table_index: 17,
        table_html: '<table><tr><td>氏名</td><td>役職</td></tr><tr><td>八馬史尚</td><td>独立社外取締役</td></tr></table>',
        heading: '取締役会',
        footnote: [
          '（注）1 和田眞治氏は、2024年12月29日に逝去され、同日をもって当社の取締役を退任いたしました。',
          '2 ジョセフ・マイケル・デビント氏は2025年3月9日に当社の取締役を辞任いたしました。',
        ],
      },
      {
        block_id: 'b001017',
        type: 'image',
        bbox: [10, 780, 120, 920],
        image_path: 'images/chart.png',
        footnote: '画像脚注 <unsafe>',
      },
    ],
  } satisfies PageContent

  const html = renderPageContentHtml(page)

  assert.match(html, /八馬史尚/)
  assert.match(html, /pdf-page-block-footnotes/)
  assert.match(html, /和田眞治氏/)
  assert.match(html, /ジョセフ・マイケル/)
  assert.match(html, /画像脚注 &lt;unsafe&gt;/)
  assert.doesNotMatch(html, /画像脚注 <unsafe>/)
})

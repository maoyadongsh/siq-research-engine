/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import type { MarketPackagesResponse } from './api.ts'

const { deriveMarketPackageRows, packagePrimaryFile } = await import('./marketPackagesPanelModel.ts')

test('deriveMarketPackageRows maps package summaries into stable rows with busy state', () => {
  const payload: MarketPackagesResponse = {
    market: 'KR',
    packages: [
      {
        market: 'KR',
        package_path: 'companies/005930-SamsungElectronics/reports/2025-annual_task-kr',
        ticker: '005930',
        company_name: 'Samsung Electronics',
        fiscal_year: 2025,
        report_type: 'annual',
        filing_id: '2025-annual_task-kr',
        paths: { report_complete: 'parser/report_complete.md', source_map: 'qa/source_map.json' },
      },
    ],
  }

  const rows = deriveMarketPackageRows(payload, 'companies/005930-SamsungElectronics/reports/2025-annual_task-kr')

  assert.equal(rows.length, 1)
  assert.equal(rows[0].id, 'companies/005930-SamsungElectronics/reports/2025-annual_task-kr')
  assert.equal(rows[0].title, '005930 Samsung Electronics')
  assert.equal(rows[0].summary, '2025 · annual · 2025-annual_task-kr')
  assert.equal(rows[0].busy, true)
})

test('packagePrimaryFile prefers report_complete as the primary file', () => {
  assert.equal(
    packagePrimaryFile({ paths: { source_map: 'qa/source_map.json', report_complete: 'parser/report_complete.md' } }),
    'parser/report_complete.md',
  )
  assert.equal(packagePrimaryFile({ paths: { source_map: 'qa/source_map.json' } }), 'qa/source_map.json')
  assert.equal(packagePrimaryFile({ paths: undefined }), 'manifest.json')
})

test('deriveMarketPackageRows does not mark packages without paths as busy by default', () => {
  const payload: MarketPackagesResponse = {
    market: 'KR',
    packages: [{ market: 'KR', filing_id: 'legacy-package' }],
  }

  const rows = deriveMarketPackageRows(payload)

  assert.equal(rows[0].id, 'legacy-package')
  assert.equal(rows[0].busy, false)
})

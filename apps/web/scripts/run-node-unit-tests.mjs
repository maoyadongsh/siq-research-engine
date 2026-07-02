import { existsSync, readdirSync, statSync } from 'node:fs'
import { dirname, relative, resolve as resolvePath } from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const projectRoot = resolvePath(dirname(fileURLToPath(import.meta.url)), '..')
const srcRoot = resolvePath(projectRoot, 'src')

function collectTestFiles(directory) {
  const entries = readdirSync(directory, { withFileTypes: true })
  const files = []

  for (const entry of entries) {
    const fullPath = resolvePath(directory, entry.name)
    if (entry.isDirectory()) {
      files.push(...collectTestFiles(fullPath))
    } else if (entry.isFile() && entry.name.endsWith('.test.ts')) {
      files.push(relative(projectRoot, fullPath))
    }
  }

  return files
}

if (!existsSync(srcRoot) || !statSync(srcRoot).isDirectory()) {
  console.error(`Missing source directory: ${srcRoot}`)
  process.exit(1)
}

const testFiles = collectTestFiles(srcRoot).sort()
if (!testFiles.length) {
  console.error('No Node unit tests found under src/**/*.test.ts')
  process.exit(1)
}

const result = spawnSync(
  process.execPath,
  ['--import', './scripts/register-node-test-alias-loader.mjs', '--test', ...testFiles],
  {
    cwd: projectRoot,
    stdio: 'inherit',
  },
)

if (result.error) {
  console.error(result.error.message)
  process.exit(1)
}

process.exit(result.status ?? 1)

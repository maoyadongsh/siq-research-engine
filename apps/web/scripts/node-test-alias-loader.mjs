import { existsSync, statSync } from 'node:fs'
import { dirname, resolve as resolvePath, sep } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const projectRoot = resolvePath(dirname(fileURLToPath(import.meta.url)), '..')
const srcRoot = resolvePath(projectRoot, 'src')
const extensions = ['', '.ts', '.tsx', '.mts', '.js', '.jsx', '.mjs', '.json']
const indexFiles = ['index.ts', 'index.tsx', 'index.mts', 'index.js', 'index.jsx', 'index.mjs', 'index.json']

function isFile(path) {
  try {
    return statSync(path).isFile()
  } catch {
    return false
  }
}

function isDirectory(path) {
  try {
    return statSync(path).isDirectory()
  } catch {
    return false
  }
}

function resolveAliasSpecifier(specifier) {
  const target = resolvePath(srcRoot, specifier.slice(2))
  if (target !== srcRoot && !target.startsWith(`${srcRoot}${sep}`)) {
    throw new Error(`Refusing to resolve alias outside src: ${specifier}`)
  }

  for (const extension of extensions) {
    const candidate = `${target}${extension}`
    if (isFile(candidate)) return candidate
  }

  if (existsSync(target) && isDirectory(target)) {
    for (const indexFile of indexFiles) {
      const candidate = resolvePath(target, indexFile)
      if (isFile(candidate)) return candidate
    }
  }

  return target
}

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith('@/')) {
    return {
      url: pathToFileURL(resolveAliasSpecifier(specifier)).href,
      shortCircuit: true,
    }
  }

  return nextResolve(specifier, context)
}

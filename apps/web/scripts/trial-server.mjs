import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { gzipSync } from 'node:zlib'
import { createProxyRules } from './proxy-config.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const rootDir = path.resolve(__dirname, '..')
const distDir = path.join(rootDir, 'dist')
const host = process.env.TRIAL_HOST || '0.0.0.0'
const port = Number(process.env.TRIAL_PORT || 1349)

const proxyRules = createProxyRules({ includeAuth: true, includeEval: true })

const contentTypes = new Map([
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.mjs', 'text/javascript; charset=utf-8'],
  ['.css', 'text/css; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.svg', 'image/svg+xml'],
  ['.png', 'image/png'],
  ['.jpg', 'image/jpeg'],
  ['.jpeg', 'image/jpeg'],
  ['.gif', 'image/gif'],
  ['.webp', 'image/webp'],
  ['.mp4', 'video/mp4'],
  ['.webm', 'video/webm'],
  ['.ico', 'image/x-icon'],
  ['.woff', 'font/woff'],
  ['.woff2', 'font/woff2'],
  ['.ttf', 'font/ttf'],
  ['.map', 'application/json; charset=utf-8'],
])

const securityHeaders = {
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'strict-origin-when-cross-origin',
}

const compressibleExtensions = new Set(['.html', '.js', '.mjs', '.css', '.json', '.svg'])

const hopByHopHeaders = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
])

function json(res, statusCode, body) {
  const payload = JSON.stringify(body, null, 2)
  res.writeHead(statusCode, {
    ...securityHeaders,
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(payload),
    'cache-control': 'no-store',
  })
  res.end(payload)
}

function findProxyRule(urlPath) {
  return proxyRules.find((rule) => urlPath === rule.prefix || urlPath.startsWith(`${rule.prefix}/`))
}

function copyHeaders(headers, hostHeader) {
  const result = {}
  for (const [key, value] of Object.entries(headers)) {
    if (!hopByHopHeaders.has(key.toLowerCase())) {
      result[key] = value
    }
  }
  result.host = hostHeader
  return result
}

function proxyRequestHeaders(req, rule, hostHeader) {
  return {
    ...copyHeaders(req.headers, hostHeader),
    ...(rule.headers || {}),
  }
}

function proxyRequest(req, res, rule, parsedUrl) {
  const target = new URL(rule.target)
  const rewrittenPath = rule.rewrite ? rule.rewrite(parsedUrl.pathname) : parsedUrl.pathname
  const targetPath = `${rewrittenPath}${parsedUrl.search}`

  const upstream = http.request(
    {
      protocol: target.protocol,
      hostname: target.hostname,
      port: target.port,
      method: req.method,
      path: targetPath,
      headers: proxyRequestHeaders(req, rule, target.host),
      timeout: 10 * 60 * 1000,
    },
    (upstreamRes) => {
      const headers = copyHeaders(upstreamRes.headers, req.headers.host || '')
      delete headers.host
      res.writeHead(upstreamRes.statusCode || 502, headers)
      upstreamRes.pipe(res)
    },
  )

  upstream.on('timeout', () => upstream.destroy(new Error('upstream timeout')))
  upstream.on('error', (error) => {
    if (!res.headersSent) {
      json(res, 502, {
        error: 'Bad Gateway',
        target: rule.target,
        detail: error.message,
      })
      return
    }
    res.destroy(error)
  })

  req.pipe(upstream)
}

function safeFilePath(urlPath) {
  let decoded = ''
  try {
    decoded = decodeURIComponent(urlPath)
  } catch {
    return null
  }
  const normalized = path.normalize(decoded).replace(/^(\.\.[/\\])+/, '')
  const filePath = path.join(distDir, normalized)
  const relative = path.relative(distDir, filePath)
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    return null
  }
  return filePath
}

function clientAcceptsGzip(req) {
  return /\bgzip\b/.test(String(req.headers['accept-encoding'] || ''))
}

function compressedFilePath(filePath) {
  const ext = path.extname(filePath).toLowerCase()
  if (!compressibleExtensions.has(ext)) return null
  return `${filePath}.gz`
}

function ensureCompressedFile(filePath, gzipPath, sourceStat) {
  try {
    const gzipStat = fs.statSync(gzipPath)
    if (gzipStat.isFile() && gzipStat.mtimeMs >= sourceStat.mtimeMs) return true
  } catch {
    // Missing or stale compressed file; regenerate below.
  }

  try {
    const payload = fs.readFileSync(filePath)
    fs.writeFileSync(gzipPath, gzipSync(payload, { level: 6 }))
    return true
  } catch {
    return false
  }
}

function sendSimpleFile(req, res, filePath, stat, headers) {
  res.writeHead(200, {
    ...headers,
    'content-length': stat.size,
  })

  if (req.method === 'HEAD') {
    res.end()
    return
  }

  fs.createReadStream(filePath).pipe(res)
}

function serveFile(req, res, filePath, cacheControl = 'public, max-age=3600') {
  fs.stat(filePath, (statError, stat) => {
    if (statError || !stat.isFile()) {
      json(res, 404, { error: 'Not Found' })
      return
    }

    const ext = path.extname(filePath).toLowerCase()
    const contentType = contentTypes.get(ext) || 'application/octet-stream'
    const baseHeaders = {
      ...securityHeaders,
      'content-type': contentType,
      'cache-control': cacheControl,
      'accept-ranges': 'bytes',
      vary: 'Accept-Encoding',
    }
    const rangeHeader = req.headers.range

    const gzipPath = compressedFilePath(filePath)
    if (!rangeHeader && gzipPath && clientAcceptsGzip(req) && ensureCompressedFile(filePath, gzipPath, stat)) {
      fs.stat(gzipPath, (gzipError, gzipStat) => {
        if (gzipError || !gzipStat.isFile()) {
          sendSimpleFile(req, res, filePath, stat, baseHeaders)
          return
        }
        sendSimpleFile(req, res, gzipPath, gzipStat, {
          ...baseHeaders,
          'content-encoding': 'gzip',
        })
      })
      return
    }

    if (typeof rangeHeader === 'string') {
      const match = /^bytes=(\d*)-(\d*)$/.exec(rangeHeader.trim())
      if (!match) {
        res.writeHead(416, {
          ...baseHeaders,
          'content-range': `bytes */${stat.size}`,
        })
        res.end()
        return
      }

      const [, rawStart, rawEnd] = match
      let start = rawStart ? Number(rawStart) : 0
      let end = rawEnd ? Number(rawEnd) : stat.size - 1

      if (!rawStart && rawEnd) {
        const suffixLength = Number(rawEnd)
        start = Math.max(stat.size - suffixLength, 0)
        end = stat.size - 1
      }

      if (
        !Number.isInteger(start) ||
        !Number.isInteger(end) ||
        start < 0 ||
        end < start ||
        start >= stat.size
      ) {
        res.writeHead(416, {
          ...baseHeaders,
          'content-range': `bytes */${stat.size}`,
        })
        res.end()
        return
      }

      end = Math.min(end, stat.size - 1)
      const chunkSize = end - start + 1
      res.writeHead(206, {
        ...baseHeaders,
        'content-length': chunkSize,
        'content-range': `bytes ${start}-${end}/${stat.size}`,
      })

      if (req.method === 'HEAD') {
        res.end()
        return
      }

      fs.createReadStream(filePath, { start, end }).pipe(res)
      return
    }

    res.writeHead(200, {
      ...baseHeaders,
      'content-length': stat.size,
    })

    if (req.method === 'HEAD') {
      res.end()
      return
    }

    fs.createReadStream(filePath).pipe(res)
  })
}

function serveStatic(req, res, parsedUrl) {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    json(res, 405, { error: 'Method Not Allowed' })
    return
  }

  const requestedPath = parsedUrl.pathname === '/' ? '/index.html' : parsedUrl.pathname
  const filePath = safeFilePath(requestedPath)
  if (!filePath) {
    json(res, 400, { error: 'Bad Request' })
    return
  }

  fs.stat(filePath, (statError, stat) => {
    if (!statError && stat.isFile()) {
      const isIndex = requestedPath === '/index.html'
      const isHashedAsset = parsedUrl.pathname.startsWith('/assets/')
      serveFile(
        req,
        res,
        filePath,
        isIndex ? 'no-cache' : isHashedAsset ? 'public, max-age=31536000, immutable' : 'public, max-age=3600',
      )
      return
    }

    const indexPath = path.join(distDir, 'index.html')
    serveFile(req, res, indexPath, 'no-cache')
  })
}

const server = http.createServer((req, res) => {
  const parsedUrl = new URL(req.url || '/', 'http://trial.local')

  if (parsedUrl.pathname === '/__trial_health') {
    json(res, 200, {
      status: 'ok',
      port,
      distDir,
      proxyRules: proxyRules.map(({ prefix, target }) => ({ prefix, target })),
    })
    return
  }

  const rule = findProxyRule(parsedUrl.pathname)
  if (rule) {
    proxyRequest(req, res, rule, parsedUrl)
    return
  }

  serveStatic(req, res, parsedUrl)
})

server.listen(port, host, () => {
  console.log(`[trial] serving ${distDir}`)
  console.log(`[trial] listening on http://${host}:${port}`)
})

import { createReadStream } from 'node:fs'
import { stat } from 'node:fs/promises'
import { createServer } from 'node:http'
import { dirname, extname, join, normalize, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { Readable } from 'node:stream'


const root = resolve(dirname(fileURLToPath(import.meta.url)), 'dist')
const backendUrl = (process.env.BACKEND_URL || 'http://backend:8000').replace(/\/$/, '')
const port = Number(process.env.PORT || 5173)
const contentTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2'
}

const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url || '/', 'http://localhost')
    if (url.pathname === '/healthz') {
      sendJson(response, 200, { status: 'ok', service: 'frontend' })
      return
    }
    if (url.pathname.startsWith('/api/')) {
      await proxyApi(request, response, url)
      return
    }
    await serveStatic(response, url.pathname)
  } catch {
    sendJson(response, 500, { message: 'frontend server error' })
  }
})

server.listen(port, '0.0.0.0', () => {
  console.log(`Agent Loop frontend listening on ${port}`)
})

async function proxyApi(request, response, url) {
  const headers = { ...request.headers }
  delete headers.host
  delete headers.connection
  delete headers['content-length']
  const method = request.method || 'GET'
  const options = { method, headers, redirect: 'manual' }
  if (!['GET', 'HEAD'].includes(method)) {
    options.body = request
    options.duplex = 'half'
  }
  const upstream = await fetch(`${backendUrl}${url.pathname}${url.search}`, options)
  const responseHeaders = {}
  upstream.headers.forEach((value, key) => {
    if (!['connection', 'keep-alive', 'transfer-encoding'].includes(key.toLowerCase())) {
      responseHeaders[key] = value
    }
  })
  response.writeHead(upstream.status, responseHeaders)
  if (upstream.body) {
    Readable.fromWeb(upstream.body).pipe(response)
  } else {
    response.end()
  }
}

async function serveStatic(response, pathname) {
  const decoded = decodeURIComponent(pathname)
  const relativePath = normalize(decoded).replace(/^([/\\])+/, '')
  let target = resolve(root, relativePath || 'index.html')
  if (!target.startsWith(`${root}\\`) && !target.startsWith(`${root}/`) && target !== root) {
    sendJson(response, 403, { message: 'forbidden' })
    return
  }
  if (!(await isFile(target))) {
    target = join(root, 'index.html')
  }
  response.writeHead(200, {
    'Content-Type': contentTypes[extname(target).toLowerCase()] || 'application/octet-stream',
    'Cache-Control': target.endsWith('index.html') ? 'no-cache' : 'public, max-age=31536000, immutable'
  })
  createReadStream(target).pipe(response)
}

async function isFile(path) {
  try {
    return (await stat(path)).isFile()
  } catch {
    return false
  }
}

function sendJson(response, status, payload) {
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' })
  response.end(JSON.stringify(payload))
}

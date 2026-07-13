export async function apiRequest(url, options = {}) {
  const response = await fetch(url, options)
  const contentType = response.headers.get('content-type') || ''
  const data = contentType.includes('application/json') ? await response.json() : await response.text()

  if (!response.ok) {
    const detail = data?.detail
    const message =
      (typeof detail === 'object' && detail?.message) ||
      (typeof detail === 'string' && detail) ||
      data?.message ||
      `请求失败 (${response.status})`
    const error = new Error(message)
    error.status = response.status
    error.payload = data
    throw error
  }
  return data
}

export function getJson(url) {
  return apiRequest(url)
}

export function postJson(url, payload) {
  return apiRequest(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
}

export function patchJson(url, payload) {
  return apiRequest(url, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
}

export function deleteJson(url, headers = {}) {
  return apiRequest(url, { method: 'DELETE', headers })
}

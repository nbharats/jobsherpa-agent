// Small wrapper around fetch for the Flask JSON API (same origin, read-only).
export async function getJson(path, params = {}, signal) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== '' && value != null),
  ).toString()
  const response = await fetch(query ? `${path}?${query}` : path, { signal })
  if (!response.ok) throw new Error(`Request failed (${response.status})`)
  return response.json()
}
import { useCallback, useEffect, useRef, useState } from 'react'
import { getJson } from './api.js'

// Loads JSON from the API and optionally refreshes it on a timer.
export function useApi(path, params = {}, refreshMs = 0) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const key = JSON.stringify([path, params])
  const controller = useRef(null)

  const load = useCallback(async () => {
    controller.current?.abort()
    const current = new AbortController()
    controller.current = current
    try {
      const result = await getJson(path, params, current.signal)
      setData(result)
      setError('')
    } catch (err) {
      if (err.name !== 'AbortError') setError(err.message)
    } finally {
      if (!current.signal.aborted) setLoading(false)
    }
    // `key` stands in for path and params, which are new objects on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  useEffect(() => {
    setLoading(true)
    load()
    if (!refreshMs) return undefined
    const timer = setInterval(load, refreshMs)
    return () => {
      clearInterval(timer)
      controller.current?.abort()
    }
  }, [load, refreshMs])

  return { data, error, loading, reload: load }
}
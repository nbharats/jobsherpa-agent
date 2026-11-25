import { useState } from 'react'
import State from '../components/State.jsx'
import { useApi } from '../hooks.js'

const LEVELS = ['ALL', 'INFO', 'WARNING', 'ERROR']

export default function Logs() {
  const [level, setLevel] = useState('ALL')
  const [lines, setLines] = useState(200)
  const [live, setLive] = useState(false)
  const { data, error, loading, reload } = useApi('/api/logs', { level, lines }, live ? 5000 : 0)

  return (
    <>
      <div className="toolbar">
        <label>
          Level{' '}
          <select value={level} onChange={(event) => setLevel(event.target.value)}>
            {LEVELS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label>
          Lines{' '}
          <input
            type="number"
            min="10"
            max="1000"
            step="10"
            value={lines}
            onChange={(event) => setLines(Number(event.target.value) || 200)}
          />
        </label>
        <label>
          <input type="checkbox" checked={live} onChange={(event) => setLive(event.target.checked)} /> Live (5 s)
        </label>
        <button type="button" onClick={reload}>
          Refresh now
        </button>
      </div>
      <State loading={loading && !data} error={error} />
      {data && (
        <pre>{data.lines.length ? data.lines.join('\n') : 'No log lines yet. Run: python main.py --run-now --dry-run'}</pre>
      )}
    </>
  )
}
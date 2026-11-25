import { useState } from 'react'
import Badge from '../components/Badge.jsx'
import State from '../components/State.jsx'
import TrendChart from '../components/TrendChart.jsx'
import { useApi } from '../hooks.js'

const METRICS = [
  ['scraped', 'Scraped'],
  ['new', 'New jobs'],
  ['applied', 'Applied'],
  ['emailed', 'Emailed'],
  ['failed', 'Failed'],
]

const percent = (value) => (value === null ? '-' : `${value}%`)

function MetricCard({ title, block, note }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      <div className="grid">
        {METRICS.map(([key, label]) => (
          <div className="metric" key={key}>
            <div className="value">{block[key]}</div>
            <div className="label">{label}</div>
          </div>
        ))}
        <div className="metric">
          <div className="value">{percent(block.success_rate)}</div>
          <div className="label">Success rate</div>
        </div>
      </div>
      {note && <p className="muted">{note}</p>}
    </section>
  )
}

export default function Overview() {
  const [live, setLive] = useState(true)
  const { data, error, loading, reload } = useApi('/api/overview', {}, live ? 10000 : 0)
  if (!data) return <State loading={loading} error={error} />

  const statuses = Object.entries(data.statuses)
  return (
    <>
      <div className="toolbar">
        <label>
          <input type="checkbox" checked={live} onChange={(event) => setLive(event.target.checked)} /> Live refresh (10 s)
        </label>
        <button type="button" onClick={reload}>
          Refresh now
        </button>
        {error && <span className="bad">Update failed: {error}</span>}
      </div>
      <MetricCard title={`Today (${data.day})`} block={data.today} />
      <MetricCard
        title="All time"
        block={data.all_time}
        note={`Across ${data.all_time.days} recorded day(s). Dry runs are not recorded.`}
      />
      <section className="card">
        <h2>Jobs by status ({data.jobs_tracked} tracked)</h2>
        {statuses.length ? (
          statuses.map(([status, count]) => (
            <span key={status} className="chip">
              <Badge value={status} /> {count}
            </span>
          ))
        ) : (
          <span className="muted">No jobs tracked yet. Run: python main.py --run-now --dry-run</span>
        )}
      </section>
      <section className="card">
        <h2>Last 7 days</h2>
        <TrendChart trend={data.trend} />
        <p className="legend">
          <span className="swatch applied" /> Applied <span className="swatch emailed" /> Emailed
        </p>
      </section>
    </>
  )
}
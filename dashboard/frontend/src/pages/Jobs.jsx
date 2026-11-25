import { useMemo, useState } from 'react'
import Badge from '../components/Badge.jsx'
import JobLink from '../components/JobLink.jsx'
import State from '../components/State.jsx'
import { useApi } from '../hooks.js'

const COLUMNS = [
  ['score', 'Score'],
  ['title', 'Job'],
  ['company', 'Company'],
  ['portal', 'Portal'],
  ['status', 'Status'],
  ['date', 'Date'],
]

function compare(a, b, key) {
  if (key === 'score') return a.score - b.score
  return String(a[key]).localeCompare(String(b[key]))
}

export default function Jobs() {
  const [status, setStatus] = useState('')
  const [portal, setPortal] = useState('')
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState({ key: null, dir: 1 })
  const { data: meta } = useApi('/api/meta')
  const { data: jobs, error, loading } = useApi('/api/jobs', { status, portal })

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const matching = (jobs ?? []).filter(
      (job) => !needle || `${job.title} ${job.company}`.toLowerCase().includes(needle),
    )
    return sort.key ? [...matching].sort((a, b) => compare(a, b, sort.key) * sort.dir) : matching
  }, [jobs, query, sort])

  const toggleSort = (key) =>
    setSort((previous) => ({ key, dir: previous.key === key ? -previous.dir : 1 }))

  return (
    <section className="card">
      <div className="toolbar">
        <label>
          Status{' '}
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">All</option>
            {(meta?.statuses ?? []).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label>
          Portal{' '}
          <select value={portal} onChange={(event) => setPortal(event.target.value)}>
            <option value="">All</option>
            {(meta?.portals ?? []).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <input
          type="search"
          placeholder="Search title or company"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <span className="muted">{rows.length} job(s)</span>
      </div>
      <State loading={loading && !jobs} error={error} />
      <div className="scroll">
        <table>
          <thead>
            <tr>
              {COLUMNS.map(([key, label]) => (
                <th key={key}>
                  <button type="button" className="sort" onClick={() => toggleSort(key)}>
                    {label}
                    {sort.key === key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((job, index) => (
              <tr key={`${job.link}-${job.title}-${index}`}>
                <td>{job.score.toFixed(1)}</td>
                <td>
                  <JobLink link={job.link} title={job.title} />
                </td>
                <td>{job.company}</td>
                <td>{job.portal}</td>
                <td>
                  <Badge value={job.status} />
                </td>
                <td>{job.date}</td>
              </tr>
            ))}
            {!rows.length && !loading && (
              <tr>
                <td colSpan={6} className="muted">
                  No jobs match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
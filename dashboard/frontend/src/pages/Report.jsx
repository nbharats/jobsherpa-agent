import State from '../components/State.jsx'
import { useApi } from '../hooks.js'

export default function Report() {
  const { data, error, loading } = useApi('/api/report')
  if (!data) return <State loading={loading} error={error} />
  if (!data.available) {
    return (
      <div className="card">
        No report yet. Run: <code>python main.py --run-now --dry-run</code>
      </div>
    )
  }
  return (
    <iframe
      src="/report/latest"
      sandbox="allow-popups allow-popups-to-escape-sandbox"
      title="Latest daily report"
    />
  )
}
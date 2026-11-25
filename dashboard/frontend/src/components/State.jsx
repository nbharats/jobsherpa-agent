// Shown while data is loading or when the request failed.
export default function State({ loading, error }) {
  if (error) return <div className="card error">Could not load data: {error}</div>
  if (loading) return <div className="card muted">Loading...</div>
  return null
}
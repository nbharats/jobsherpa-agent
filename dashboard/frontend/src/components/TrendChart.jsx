// Stacked bars (applied + emailed per day), drawn as SVG without a chart library.
export default function TrendChart({ trend }) {
  if (!trend.length) return <p className="muted">No history yet.</p>
  const width = 640
  const height = 200
  const pad = 28
  const peak = Math.max(1, ...trend.map((day) => day.applied + day.emailed))
  const slot = (width - pad * 2) / trend.length
  const barWidth = Math.min(48, slot * 0.6)
  const base = height - pad
  const scale = (height - pad * 2) / peak

  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Applied and emailed per day" className="chart">
      <line x1={pad} y1={base} x2={width - pad} y2={base} className="axis-line" />
      {trend.map((day, index) => {
        const x = pad + index * slot + (slot - barWidth) / 2
        const applied = day.applied * scale
        const emailed = day.emailed * scale
        return (
          <g key={day.date}>
            <title>{`${day.date}: ${day.applied} applied, ${day.emailed} emailed, ${day.failed} failed`}</title>
            <rect x={x} y={base - applied} width={barWidth} height={applied} className="bar-applied" />
            <rect x={x} y={base - applied - emailed} width={barWidth} height={emailed} className="bar-emailed" />
            <text x={x + barWidth / 2} y={height - 8} textAnchor="middle" className="axis-text">
              {day.date.slice(5)}
            </text>
          </g>
        )
      })}
    </svg>
  )
}
const KNOWN = ['new', 'in_progress', 'applied', 'email_sent', 'failed', 'manual_required', 'pending', 'sent', 'invalid']

export default function Badge({ value }) {
  return <span className={`badge ${KNOWN.includes(value) ? value : ''}`}>{value}</span>
}
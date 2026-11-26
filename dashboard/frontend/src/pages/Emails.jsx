import Badge from '../components/Badge.jsx'
import JobLink from '../components/JobLink.jsx'
import State from '../components/State.jsx'
import { useApi } from '../hooks.js'

export default function Emails() {
  const { data, error, loading } = useApi('/api/emails', {}, 15000)
  if (!data) return <State loading={loading} error={error} />

  return (
    <>
      <section className="card">
        <h2>Send log</h2>
        <p className="muted">
          Every real send attempt with its full text. Dry runs are not logged. When EMAIL_REDIRECT_TO is set, the
          email went to your test address, not to the company.
        </p>
        {data.log.map((row, index) => (
          <details key={`${row.sent_at}-${index}`}>
            <summary>
              {row.sent_at} · {row.kind} · {row.company} · <Badge value={row.status} /> · {row.subject}
            </summary>
            <p className="muted">
              To: {row.to}
              {row.delivered_to && row.delivered_to !== row.to ? ` (delivered to ${row.delivered_to})` : ''}
            </p>
            <pre>{row.body}</pre>
          </details>
        ))}
        {!data.log.length && <p className="muted">No real emails sent yet.</p>}
      </section>

      <section className="card">
        <h2>Cold emails by job</h2>
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Company</th>
                <th>Job</th>
                <th>HR address</th>
                <th>Status</th>
                <th>Date</th>
              </tr>
            </thead>
            <tbody>
              {data.cold.map((row, index) => (
                <tr key={`${row.hr_email}-${index}`}>
                  <td>{row.company}</td>
                  <td>
                    <JobLink link={row.link} title={row.title} />
                  </td>
                  <td>{row.hr_email}</td>
                  <td>
                    <Badge value={row.status} />
                  </td>
                  <td>{row.date}</td>
                </tr>
              ))}
              {!data.cold.length && (
                <tr>
                  <td colSpan={5} className="muted">
                    No cold emails yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card">
        <h2>HR drip campaign</h2>
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Name</th>
                <th>Company</th>
                <th>Designation</th>
                <th>Status</th>
                <th>Date</th>
              </tr>
            </thead>
            <tbody>
              {data.drip.map((row) => (
                <tr key={row.email}>
                  <td>{row.email}</td>
                  <td>{row.name}</td>
                  <td>{row.company}</td>
                  <td>{row.designation}</td>
                  <td>
                    <Badge value={row.status} />
                  </td>
                  <td>{row.date}</td>
                </tr>
              ))}
              {!data.drip.length && (
                <tr>
                  <td colSpan={6} className="muted">
                    No drip emails sent yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </>
  )
}
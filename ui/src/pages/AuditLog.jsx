import { useState, useEffect } from 'react'
import api from '../api'

function fmtDate(ts) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString('en-US', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

const ACTION_COLORS = {
  lookup:       'badge-blue',
  search:       'badge-purple',
  fuzzy_search: 'badge-orange',
  deliver:      'badge-green',
  export:       'badge-gray',
}

export default function AuditLog() {
  const [logs, setLogs]     = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState(null)

  useEffect(() => {
    api.getAuditLog(100)
      .then(setLogs)
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">Audit Log</h1>
        <p className="page-subtitle">Every CS rep lookup is tracked for compliance — supervisor access only</p>
      </div>

      {loading && <div className="loading">Loading audit log…</div>}
      {error   && <div className="alert alert-error">{error}</div>}

      {logs !== null && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Recent Activity</span>
            <span className="badge badge-gray">{logs.length} entries</span>
          </div>
          {logs.length === 0 ? (
            <p className="empty-message">No audit entries yet — actions will appear here once reps start using the app</p>
          ) : (
            <div className="receipt-table-wrapper">
              <table className="receipt-table">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Rep</th>
                    <th>Role</th>
                    <th>Action</th>
                    <th>Resource</th>
                    <th>Results</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map((log, i) => (
                    <tr key={log.audit_id || i}>
                      <td className="td-date">{fmtDate(log.created_at)}</td>
                      <td>{log.rep_email}</td>
                      <td><span className="badge badge-gray">{log.rep_role}</span></td>
                      <td>
                        <span className={`badge ${ACTION_COLORS[log.action] || 'badge-gray'}`}>
                          {log.action}
                        </span>
                      </td>
                      <td>
                        {log.resource_type}
                        {log.resource_id ? <code style={{ marginLeft: 6, fontSize: 11, color: 'var(--text-muted)' }}>{log.resource_id}</code> : ''}
                      </td>
                      <td>{log.result_count ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

import { useState } from 'react'
import api from '../api'
import ReceiptModal from '../components/ReceiptModal'

const STORES = ['', 'East Liberty', 'Shadyside', 'Squirrel Hill', 'Monroeville']

function fmt$(cents) {
  if (cents == null) return '—'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100)
}

function fmtDate(ts) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function TenderBadge({ type }) {
  const cls = { CREDIT: 'badge-blue', DEBIT: 'badge-purple', CASH: 'badge-green', EBT: 'badge-orange' }
  return <span className={`badge ${cls[type] || 'badge-gray'}`}>{type || '—'}</span>
}

export default function FuzzySearch() {
  const [form, setForm] = useState({
    customer_id: '', store_name: '', date_from: '', date_to: '',
    amount_min: '', amount_max: '', card_last4: '',
  })
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)

  const set = (e) => setForm(f => ({ ...f, [e.target.name]: e.target.value }))

  const handleSearch = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const p = {}
      if (form.customer_id)  p.customer_id  = form.customer_id
      if (form.store_name)   p.store_name   = form.store_name
      if (form.date_from)    p.date_from    = form.date_from
      if (form.date_to)      p.date_to      = form.date_to
      if (form.amount_min)   p.amount_min   = parseFloat(form.amount_min)
      if (form.amount_max)   p.amount_max   = parseFloat(form.amount_max)
      if (form.card_last4)   p.card_last4   = form.card_last4
      p.limit = 25
      const data = await api.fuzzySearch(p)
      setResults(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const clear = () => {
    setForm({ customer_id: '', store_name: '', date_from: '', date_to: '', amount_min: '', amount_max: '', card_last4: '' })
    setResults(null)
    setError(null)
  }

  const handleViewReceipt = async (transactionId) => {
    try {
      const fullReceipt = await api.getReceipt(transactionId)
      setSelected(fullReceipt)
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">Receipt Search</h1>
        <p className="page-subtitle">
          Fill in whatever the customer provides — all fields are optional. Amounts in dollars.
        </p>
      </div>

      <div className="search-layout">
        {/* ── Sidebar form ── */}
        <aside className="search-sidebar">
          <form onSubmit={handleSearch} className="search-form">

            <div className="form-section">
              <div className="form-section-title">Customer</div>
              <div className="form-group">
                <label>Customer ID</label>
                <input name="customer_id" value={form.customer_id} onChange={set} placeholder="cust-5001" />
              </div>
              <div className="form-group">
                <label>Card Last 4 Digits</label>
                <input name="card_last4" value={form.card_last4} onChange={set} placeholder="4532" maxLength={4} />
              </div>
            </div>

            <div className="form-section">
              <div className="form-section-title">Store &amp; Date</div>
              <div className="form-group">
                <label>Store</label>
                <select name="store_name" value={form.store_name} onChange={set}>
                  {STORES.map(s => <option key={s} value={s}>{s || 'Any store'}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label>From</label>
                <input type="date" name="date_from" value={form.date_from} onChange={set} />
              </div>
              <div className="form-group">
                <label>To</label>
                <input type="date" name="date_to" value={form.date_to} onChange={set} />
              </div>
            </div>

            <div className="form-section">
              <div className="form-section-title">Amount Range ($)</div>
              <div className="form-row">
                <div className="form-group">
                  <label>Min</label>
                  <input type="number" name="amount_min" value={form.amount_min} onChange={set} placeholder="30" min="0" step="0.01" />
                </div>
                <div className="form-group">
                  <label>Max</label>
                  <input type="number" name="amount_max" value={form.amount_max} onChange={set} placeholder="60" min="0" step="0.01" />
                </div>
              </div>
              <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                Single bound auto-expands ±10%
              </p>
            </div>

            <div className="form-actions">
              <button type="submit" className="btn btn-primary" disabled={loading}>
                {loading ? 'Searching…' : '🔍  Search Receipts'}
              </button>
              <button type="button" className="btn btn-secondary" onClick={clear}>
                Clear
              </button>
            </div>
          </form>
        </aside>

        {/* ── Results ── */}
        <div className="search-results">
          {error && <div className="alert alert-error">{error}</div>}

          {results === null && !loading && (
            <div className="empty-state">
              <div className="empty-icon">🧾</div>
              <p>Fill in any fields and click <strong>Search Receipts</strong></p>
              <p className="empty-hint" style={{ marginTop: 8 }}>
                Example: Store = East Liberty, Amount $30–$40, Card = 4532
              </p>
            </div>
          )}

          {results !== null && (
            <>
              <div className="results-header">
                <span className="results-count">
                  {results.count} receipt{results.count !== 1 ? 's' : ''} found
                </span>
              </div>

              {results.count === 0 ? (
                <div className="empty-state">
                  <div className="empty-icon">🔍</div>
                  <p>No receipts matched those search criteria</p>
                </div>
              ) : (
                <div className="card">
                  <div className="receipt-table-wrapper">
                    <table className="receipt-table">
                      <thead>
                        <tr>
                          <th>Date &amp; Time</th>
                          <th>Store</th>
                          <th>Items</th>
                          <th>Total</th>
                          <th>Tender</th>
                          <th>Card</th>
                          <th></th>
                        </tr>
                      </thead>
                      <tbody>
                        {results.results.map(r => (
                          <tr key={r.transaction_id} className="clickable-row" onClick={() => handleViewReceipt(r.transaction_id)}>
                            <td className="td-date">{fmtDate(r.transaction_ts)}</td>
                            <td>{r.store_name}</td>
                            <td className="td-items">{r.item_summary}</td>
                            <td className="td-amount">{fmt$(r.total_cents)}</td>
                            <td><TenderBadge type={r.tender_type} /></td>
                            <td>{r.card_last4 ? `*${r.card_last4}` : '—'}</td>
                            <td>
                              <button
                                className="btn-link"
                                onClick={e => { e.stopPropagation(); handleViewReceipt(r.transaction_id) }}
                              >
                                View →
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {selected && <ReceiptModal receipt={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}

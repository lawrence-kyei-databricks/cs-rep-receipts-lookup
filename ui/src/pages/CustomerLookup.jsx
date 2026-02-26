import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import api from '../api'
import ReceiptModal from '../components/ReceiptModal'
import CustomerCard from '../components/CustomerCard'

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

const SEED_CUSTOMERS = [
  { id: 'cust-5001', name: 'Maria Santos',    tier: 'GOLD'   },
  { id: 'cust-5002', name: 'James Chen',      tier: 'SILVER' },
  { id: 'cust-5003', name: 'Sarah Williams',  tier: 'GOLD'   },
  { id: 'cust-5004', name: 'Robert Johnson',  tier: 'BASIC'  },
  { id: 'cust-5005', name: 'Lisa Washington', tier: 'BASIC'  },
  { id: 'cust-5006', name: 'David Thompson',  tier: 'SILVER' },
]

export default function CustomerLookup() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [inputId, setInputId] = useState(id || '')
  const [customer, setCustomer] = useState(null)
  const [receipts, setReceipts] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    if (id) { setInputId(id); loadCustomer(id) }
    else { setCustomer(null); setReceipts(null); setError(null) }
  }, [id])

  const loadCustomer = async (cid) => {
    setLoading(true)
    setError(null)
    setCustomer(null)
    setReceipts(null)
    try {
      const [ctx, recs] = await Promise.allSettled([
        api.getCustomerContext(cid),
        api.getCustomerReceipts(cid),
      ])
      if (ctx.status === 'fulfilled')      setCustomer(ctx.value)
      if (recs.status === 'fulfilled')     setReceipts(recs.value)
      else                                 throw new Error(recs.reason?.message || 'Failed to load receipts')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleSearch = (e) => {
    e.preventDefault()
    const cid = inputId.trim()
    if (cid) navigate(`/customer/${cid}`)
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
        <h1 className="page-title">Customer Lookup</h1>
        <p className="page-subtitle">Enter a customer ID to pull up their profile and full receipt history</p>
      </div>

      {/* Search bar */}
      <form onSubmit={handleSearch} className="customer-search-bar">
        <input
          type="text"
          value={inputId}
          onChange={e => setInputId(e.target.value)}
          placeholder="Customer ID (e.g. cust-5001)"
          className="customer-search-input"
          autoFocus
        />
        <button type="submit" className="btn btn-primary" disabled={loading || !inputId.trim()}>
          {loading ? 'Loading…' : 'Look Up'}
        </button>
      </form>

      {error && <div className="alert alert-error">{error}</div>}

      {/* Customer context card */}
      {customer && <CustomerCard customer={customer} />}

      {/* Receipt history table */}
      {receipts !== null && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Receipt History</span>
            <span className="badge badge-gray">{receipts.length} transactions</span>
          </div>
          {receipts.length === 0 ? (
            <p className="empty-message">No receipts found for this customer</p>
          ) : (
            <div className="receipt-table-wrapper">
              <table className="receipt-table">
                <thead>
                  <tr>
                    <th>Date &amp; Time</th>
                    <th>Store</th>
                    <th>Items</th>
                    <th>Total</th>
                    <th>Payment</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {receipts.map(r => (
                    <tr key={r.transaction_id} className="clickable-row" onClick={() => handleViewReceipt(r.transaction_id)}>
                      <td className="td-date">{fmtDate(r.transaction_ts)}</td>
                      <td>{r.store_name}</td>
                      <td className="td-items">{r.item_summary}</td>
                      <td className="td-amount">{fmt$(r.total_cents)}</td>
                      <td>
                        {r.tender_type}
                        {r.card_last4 ? ` *${r.card_last4}` : ''}
                      </td>
                      <td>
                        <button className="btn-link" onClick={e => { e.stopPropagation(); handleViewReceipt(r.transaction_id) }}>
                          View →
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Empty state with quick-access buttons */}
      {!id && !loading && (
        <div className="empty-state">
          <div className="empty-icon">👤</div>
          <p>Enter a customer ID above to see their profile and receipt history</p>
          <div className="quick-links">
            <p className="empty-hint">Quick access:</p>
            {SEED_CUSTOMERS.map(c => (
              <button
                key={c.id}
                className="btn-chip"
                onClick={() => navigate(`/customer/${c.id}`)}
                title={c.name}
              >
                {c.id} — {c.name}
              </button>
            ))}
          </div>
        </div>
      )}

      {selected && <ReceiptModal receipt={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}

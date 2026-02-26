import { useState } from 'react'
import api from '../api'

function fmt$(cents) {
  if (cents == null) return '—'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100)
}

function fmtDate(ts) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric',
    year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

export default function ReceiptModal({ receipt, onClose }) {
  const [email, setEmail] = useState('')
  const [delivering, setDelivering] = useState(false)
  const [msg, setMsg] = useState(null)

  // DEBUG: Log receipt data to console
  console.log('ReceiptModal - Full receipt:', receipt)
  console.log('ReceiptModal - Line items:', receipt?.line_items)
  console.log('ReceiptModal - Line items length:', receipt?.line_items?.length)
  console.log('ReceiptModal - Item summary:', receipt?.item_summary)

  const deliver = async (method) => {
    setDelivering(true)
    setMsg(null)
    try {
      await api.deliverReceipt(
        receipt.transaction_id,
        receipt.customer_id,
        method,
        method === 'email' ? email : null,
      )
      setMsg({
        ok: true,
        text: method === 'email' ? `Receipt emailed to ${email}` : 'Sent to printer',
      })
    } catch (err) {
      setMsg({ ok: false, text: err.message })
    } finally {
      setDelivering(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div className="modal-header">
          <div>
            <div className="modal-title">Receipt Detail</div>
            <code className="txn-id">{receipt.transaction_id}</code>
          </div>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="modal-body">
          {/* Meta grid */}
          <div className="receipt-meta-grid">
            <div className="meta-item">
              <span className="meta-label">Date &amp; Time</span>
              <span className="meta-value">{fmtDate(receipt.transaction_ts)}</span>
            </div>
            <div className="meta-item">
              <span className="meta-label">Store</span>
              <span className="meta-value">{receipt.store_name}{receipt.store_id ? ` (#${receipt.store_id})` : ''}</span>
            </div>
            <div className="meta-item">
              <span className="meta-label">Payment</span>
              <span className="meta-value">
                {receipt.tender_type}
                {receipt.card_last4 ? ` ending in ${receipt.card_last4}` : ''}
              </span>
            </div>
            <div className="meta-item">
              <span className="meta-label">Customer</span>
              <span className="meta-value">{receipt.customer_name || receipt.customer_id || '—'}</span>
            </div>
          </div>

          {/* Items */}
          {(receipt.line_items?.length > 0 || receipt.item_summary) && (
            <div className="items-section">
              <span className="section-label">Items ({receipt.item_count || 0})</span>
              {receipt.line_items && receipt.line_items.length > 0 ? (
                <div className="items-list">
                  {receipt.line_items.map((item, idx) => (
                    <div key={idx} className="item-line">
                      <span className="item-name">{item.name}</span>
                      <span className="item-price">{fmt$(item.price_cents)}</span>
                    </div>
                  ))}
                </div>
              ) : receipt.item_summary ? (
                <div className="items-text">{receipt.item_summary}</div>
              ) : null}
            </div>
          )}

          {/* Totals */}
          <div className="totals-section">
            <div className="total-row">
              <span>Subtotal</span>
              <span>{fmt$(receipt.subtotal_cents)}</span>
            </div>
            <div className="total-row">
              <span>Tax</span>
              <span>{fmt$(receipt.tax_cents)}</span>
            </div>
            <div className="total-row total-final">
              <span>Total</span>
              <span>{fmt$(receipt.total_cents)}</span>
            </div>
          </div>

          {/* Delivery */}
          <div className="delivery-section">
            <span className="section-label">Deliver Receipt to Customer</span>
            <div className="delivery-form">
              <input
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="customer@email.com"
                className="delivery-email-input"
              />
              <button
                className="btn btn-primary"
                onClick={() => deliver('email')}
                disabled={!email || delivering}
              >
                📧 Email
              </button>
              <button
                className="btn btn-secondary"
                onClick={() => deliver('print')}
                disabled={delivering}
              >
                🖨️ Print
              </button>
            </div>
            {msg && (
              <div className={`alert ${msg.ok ? 'alert-success' : 'alert-error'}`} style={{ marginTop: 12 }}>
                {msg.text}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

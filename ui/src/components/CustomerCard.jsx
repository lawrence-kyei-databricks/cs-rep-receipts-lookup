function fmt$(cents) {
  if (cents == null) return null
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100)
}

function TierBadge({ tier }) {
  const cls = { GOLD: 'tier-gold', SILVER: 'tier-silver', BASIC: 'tier-basic' }
  return <span className={`tier-badge ${cls[tier] || 'tier-basic'}`}>{tier}</span>
}

export default function CustomerCard({ customer }) {
  if (!customer) return null
  const name = [customer.first_name, customer.last_name].filter(Boolean).join(' ') || customer.customer_id
  const cats = customer.top_categories ? customer.top_categories.split(',').map(s => s.trim()) : []

  return (
    <div className="card customer-card">
      <div className="customer-card-header">
        <div className="customer-avatar">{name.charAt(0).toUpperCase()}</div>
        <div>
          <div className="customer-name">{name}</div>
          <div className="customer-meta">
            {customer.loyalty_tier && <TierBadge tier={customer.loyalty_tier} />}
            {customer.preferred_store_name && (
              <span>📍 {customer.preferred_store_name}</span>
            )}
            {customer.email && <span>✉ {customer.email}</span>}
          </div>
        </div>
      </div>

      <div className="stats-grid">
        {fmt$(customer.lifetime_spend_cents) && (
          <div className="stat-card">
            <span className="stat-value">{fmt$(customer.lifetime_spend_cents)}</span>
            <span className="stat-label">Lifetime Spend</span>
          </div>
        )}
        {fmt$(customer.avg_basket_cents) && (
          <div className="stat-card">
            <span className="stat-value">{fmt$(customer.avg_basket_cents)}</span>
            <span className="stat-label">Avg Basket</span>
          </div>
        )}
        {customer.visit_frequency_days && (
          <div className="stat-card">
            <span className="stat-value">Every {customer.visit_frequency_days}d</span>
            <span className="stat-label">Visit Frequency</span>
          </div>
        )}
        {customer.member_since_date && (
          <div className="stat-card">
            <span className="stat-value">{new Date(customer.member_since_date).getFullYear()}</span>
            <span className="stat-label">Member Since</span>
          </div>
        )}
      </div>

      {cats.length > 0 && (
        <div className="top-categories">
          <span className="section-label" style={{ marginRight: 8 }}>Top Categories:</span>
          {cats.map(cat => <span key={cat} className="cat-chip">{cat}</span>)}
        </div>
      )}
    </div>
  )
}

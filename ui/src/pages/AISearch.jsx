import { useState } from 'react'
import api from '../api'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const EXAMPLES = [
  { label: 'Fancy cheese purchase', query: 'that fancy cheese I bought', cust: 'cust-5003' },
  { label: 'Ribeye steak', query: 'ribeye steak last week', cust: 'cust-5003' },
  { label: 'Dairy spending', query: 'how much have I spent on dairy', cust: 'cust-5001' },
  { label: 'East Liberty ~$35', query: 'purchase at East Liberty around $35', cust: 'cust-5001' },
]

export default function AISearch() {
  const [customerId, setCustomerId] = useState('')
  const [query, setQuery]           = useState('')
  const [result, setResult]         = useState(null)
  const [loading, setLoading]       = useState(false)
  const [error, setError]           = useState(null)
  const [showDebug, setShowDebug]   = useState(false)

  const handleSearch = async (e) => {
    e.preventDefault()
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    try {
      const data = await api.aiSearch(query, customerId.trim() || null)
      setResult(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const fill = (ex) => { setCustomerId(ex.cust); setQuery(ex.query) }

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">AI Search ✨</h1>
        <p className="page-subtitle">
          Natural language receipt search — describe what the customer bought and let AI find it
        </p>
      </div>

      <div className="card">
        <form onSubmit={handleSearch} className="ai-search-form">
          <div className="form-row" style={{ alignItems: 'flex-end' }}>
            <div className="form-group" style={{ flex: '0 0 200px' }}>
              <label>Customer ID <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>(optional)</span></label>
              <input
                type="text"
                value={customerId}
                onChange={e => setCustomerId(e.target.value)}
                placeholder="cust-5001"
              />
            </div>
            <div className="form-group" style={{ flex: 1 }}>
              <label>What did the customer buy?</label>
              <input
                type="text"
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder="e.g. ribeye steak, fancy cheese, or 'purchase at East Liberty around $35'"
                required
              />
            </div>
            <div className="form-group" style={{ flex: '0 0 auto' }}>
              <button type="submit" className="btn btn-primary" disabled={loading}>
                {loading ? 'Searching…' : '✨ Search'}
              </button>
            </div>
          </div>
        </form>

        <div className="example-queries">
          <span className="example-label">Try an example:</span>
          {EXAMPLES.map(ex => (
            <button key={ex.label} className="btn-chip" onClick={() => fill(ex)}>
              {ex.label}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {result && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">AI Search Result</span>
            <button
              className="btn-chip"
              onClick={() => setShowDebug(!showDebug)}
              style={{ marginLeft: 'auto' }}
            >
              {showDebug ? 'Hide Debug' : 'Show Debug'}
            </button>
          </div>
          <div className="ai-result-content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {result.answer || 'No answer returned'}
            </ReactMarkdown>
          </div>
          {showDebug && (
            <details open style={{ marginTop: '1rem' }}>
              <summary style={{ cursor: 'pointer', fontWeight: 'bold', marginBottom: '0.5rem' }}>
                Debug Info (Raw JSON)
              </summary>
              <pre className="ai-result">{JSON.stringify(result, null, 2)}</pre>
            </details>
          )}
        </div>
      )}
    </div>
  )
}

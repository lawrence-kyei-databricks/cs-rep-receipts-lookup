// Giant Eagle CS API client — same origin as FastAPI backend

async function request(url, options = {}) {
  const res = await fetch(url, options)
  if (!res.ok) {
    const text = await res.text()
    let msg
    try { msg = JSON.parse(text)?.detail || text } catch { msg = text }
    throw new Error(msg || `HTTP ${res.status}`)
  }
  return res.json()
}

const api = {
  async getReceipt(transactionId) {
    return request(`/receipt/${transactionId}`)
  },

  async getCustomerReceipts(customerId, limit = 20) {
    return request(`/receipt/customer/${customerId}?limit=${limit}`)
  },

  async fuzzySearch(params) {
    return request('/search/fuzzy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
  },

  async getCustomerContext(customerId) {
    return request(`/cs/context/${customerId}`)
  },

  async aiSearch(query, customerId, conversationHistory = null) {
    return request('/search/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query,
        customer_id: customerId,
        conversation_history: conversationHistory,
      }),
    })
  },

  async deliverReceipt(transactionId, customerId, method, email = null) {
    return request('/receipt/deliver', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        transaction_id: transactionId,
        customer_id: customerId,
        delivery_method: method,
        email_address: email,
      }),
    })
  },

  async getAuditLog(limit = 50) {
    return request(`/audit/log?limit=${limit}`)
  },

  async askGenie(question, customerId = null) {
    return request('/genie/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        customer_id: customerId,
      }),
    })
  },

  async askGenieFollowup(conversationId, question, customerId = null) {
    return request('/genie/followup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_id: conversationId,
        question,
        customer_id: customerId,
      }),
    })
  },
}

export default api

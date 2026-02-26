import { useState, useRef, useEffect } from 'react'
import api from '../api'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const EXAMPLES = [
  { label: 'East Liberty receipts', query: 'Show me receipts from East Liberty store last Tuesday' },
  { label: 'January spending', query: 'What did customer cust-5001 spend in January 2026?' },
  { label: 'High-value receipts', query: 'Find receipts over $50 from Store 247 this week' },
  { label: 'Dairy purchases', query: 'Show me all transactions with dairy products last month' },
]

export default function AskGenie() {
  const [customerId, setCustomerId] = useState('cust-5001')
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [conversationId, setConversationId] = useState(null)
  const [showSqlForMsg, setShowSqlForMsg] = useState({})
  const [showDataForMsg, setShowDataForMsg] = useState({})
  const messagesEndRef = useRef(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  const handleSend = async (questionText = null) => {
    const question = questionText || input
    if (!question.trim()) return

    // Add user message to chat
    const userMessage = {
      role: 'user',
      content: question,
      timestamp: new Date().toISOString(),
    }
    setMessages(prev => [...prev, userMessage])
    setInput('')
    setLoading(true)

    try {
      let data
      if (conversationId) {
        // Follow-up question
        data = await api.askGenieFollowup(conversationId, question, customerId || null)
      } else {
        // First question
        data = await api.askGenie(question, customerId || null)
        if (data.conversation_id) {
          setConversationId(data.conversation_id)
        }
      }

      // Add Genie response to chat
      const assistantMessage = {
        role: 'assistant',
        content: data.answer || 'No answer returned',
        sql: data.sql,
        data: data.data,
        row_count: data.row_count,
        status: data.status,
        timestamp: new Date().toISOString(),
        messageId: data.message_id || Date.now(),
      }
      setMessages(prev => [...prev, assistantMessage])
    } catch (err) {
      // Add error message
      const errorMessage = {
        role: 'error',
        content: err.message,
        timestamp: new Date().toISOString(),
      }
      setMessages(prev => [...prev, errorMessage])
    } finally {
      setLoading(false)
    }
  }

  const handleNewConversation = () => {
    setMessages([])
    setConversationId(null)
    setShowSqlForMsg({})
    setShowDataForMsg({})
    setInput('')
  }

  const toggleSql = (msgId) => {
    setShowSqlForMsg(prev => ({ ...prev, [msgId]: !prev[msgId] }))
  }

  const toggleData = (msgId) => {
    setShowDataForMsg(prev => ({ ...prev, [msgId]: !prev[msgId] }))
  }

  return (
    <div className="page" style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 3rem)' }}>
      <div className="page-header" style={{ flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <h1 className="page-title">Ask Genie 🧞</h1>
            <p className="page-subtitle">
              Natural language SQL queries via Databricks Genie — conversational interface
            </p>
          </div>
          <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <label style={{ fontSize: '0.9rem', fontWeight: '500' }}>Customer ID:</label>
              <input
                type="text"
                value={customerId}
                onChange={e => setCustomerId(e.target.value)}
                placeholder="cust-5001"
                style={{ width: '120px', padding: '0.4rem 0.6rem', fontSize: '0.9rem' }}
              />
            </div>
            {conversationId && (
              <button
                className="btn btn-secondary"
                onClick={handleNewConversation}
                style={{ fontSize: '0.9rem', padding: '0.4rem 0.8rem' }}
              >
                🔄 New Chat
              </button>
            )}
          </div>
        </div>
      </div>

      {messages.length === 0 && (
        <div className="card" style={{ flexShrink: 0, marginBottom: '1rem' }}>
          <div style={{ marginBottom: '0.75rem', fontWeight: '500', color: '#555' }}>
            Try an example:
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
            {EXAMPLES.map(ex => (
              <button
                key={ex.label}
                className="btn-chip"
                onClick={() => handleSend(ex.query)}
                disabled={loading}
              >
                {ex.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Chat messages */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '1rem 0',
          display: 'flex',
          flexDirection: 'column',
          gap: '1rem',
        }}
      >
        {messages.map((msg, idx) => (
          <div
            key={idx}
            style={{
              display: 'flex',
              justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
            }}
          >
            <div
              style={{
                maxWidth: '75%',
                padding: '1rem',
                borderRadius: '8px',
                background: msg.role === 'user' ? '#007bff' : msg.role === 'error' ? '#dc3545' : '#f8f9fa',
                color: msg.role === 'user' ? 'white' : msg.role === 'error' ? 'white' : 'inherit',
                boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
              }}
            >
              <div style={{ fontSize: '0.85rem', opacity: 0.8, marginBottom: '0.5rem' }}>
                {msg.role === 'user' ? '👤 You' : msg.role === 'error' ? '⚠️ Error' : '🧞 Genie'}
                {' · '}
                {new Date(msg.timestamp).toLocaleTimeString()}
              </div>

              {msg.role === 'assistant' ? (
                <>
                  <div style={{ marginBottom: '0.75rem' }}>
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                  </div>

                  {msg.sql && (
                    <div style={{ marginTop: '0.75rem' }}>
                      <button
                        className="btn-chip"
                        onClick={() => toggleSql(msg.messageId)}
                        style={{ fontSize: '0.85rem', marginBottom: '0.5rem' }}
                      >
                        {showSqlForMsg[msg.messageId] ? '📋 Hide SQL' : '📋 Show SQL'}
                      </button>
                      {showSqlForMsg[msg.messageId] && (
                        <pre
                          style={{
                            background: '#2d2d2d',
                            color: '#f8f8f2',
                            padding: '0.75rem',
                            borderRadius: '4px',
                            overflow: 'auto',
                            fontSize: '0.85rem',
                            marginTop: '0.5rem',
                          }}
                        >
                          {msg.sql}
                        </pre>
                      )}
                    </div>
                  )}

                  {msg.data && msg.data.length > 0 && (
                    <div style={{ marginTop: '0.75rem' }}>
                      <button
                        className="btn-chip"
                        onClick={() => toggleData(msg.messageId)}
                        style={{ fontSize: '0.85rem', marginBottom: '0.5rem' }}
                      >
                        {showDataForMsg[msg.messageId] ? '📊 Hide Data' : `📊 View Data (${msg.row_count} rows)`}
                      </button>
                      {showDataForMsg[msg.messageId] && (
                        <div style={{ overflow: 'auto', maxHeight: '300px', marginTop: '0.5rem' }}>
                          <table
                            style={{
                              width: '100%',
                              borderCollapse: 'collapse',
                              fontSize: '0.85rem',
                              background: 'white',
                            }}
                          >
                            <thead>
                              <tr style={{ background: '#e9ecef', position: 'sticky', top: 0 }}>
                                {Object.keys(msg.data[0]).map(col => (
                                  <th
                                    key={col}
                                    style={{
                                      padding: '0.5rem',
                                      textAlign: 'left',
                                      borderBottom: '2px solid #dee2e6',
                                      fontWeight: '600',
                                    }}
                                  >
                                    {col}
                                  </th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {msg.data.map((row, i) => (
                                <tr key={i} style={{ borderBottom: '1px solid #dee2e6' }}>
                                  {Object.values(row).map((val, j) => (
                                    <td key={j} style={{ padding: '0.5rem' }}>
                                      {val !== null ? String(val) : <span style={{ color: '#999' }}>NULL</span>}
                                    </td>
                                  ))}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )}
                </>
              ) : (
                <div style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</div>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
            <div
              style={{
                padding: '1rem',
                borderRadius: '8px',
                background: '#f8f9fa',
                boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
              }}
            >
              <div style={{ fontSize: '0.85rem', opacity: 0.8, marginBottom: '0.5rem' }}>🧞 Genie</div>
              <div style={{ color: '#666' }}>Thinking...</div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className="card" style={{ flexShrink: 0, marginTop: '1rem' }}>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            handleSend()
          }}
          style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}
        >
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder={conversationId ? "Ask a follow-up question..." : "Ask a question about receipts or spending..."}
            disabled={loading}
            style={{ flex: 1, padding: '0.75rem', fontSize: '1rem' }}
          />
          <button
            type="submit"
            className="btn btn-primary"
            disabled={loading || !input.trim()}
            style={{ padding: '0.75rem 1.5rem', fontSize: '1rem' }}
          >
            {loading ? 'Sending...' : '🧞 Send'}
          </button>
        </form>
      </div>
    </div>
  )
}

import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import FuzzySearch from './pages/FuzzySearch'
import CustomerLookup from './pages/CustomerLookup'
import AISearch from './pages/AISearch'
import AuditLog from './pages/AuditLog'
import ApiDocs from './pages/ApiDocs'

// Using HashRouter to support page refresh on all routes
export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/search" replace />} />
          <Route path="search" element={<FuzzySearch />} />
          <Route path="customer" element={<CustomerLookup />} />
          <Route path="customer/:id" element={<CustomerLookup />} />
          <Route path="ai-search" element={<AISearch />} />
          <Route path="audit" element={<AuditLog />} />
          <Route path="api-docs" element={<ApiDocs />} />
        </Route>
      </Routes>
    </HashRouter>
  )
}

import { NavLink, Outlet } from 'react-router-dom'

const navItems = [
  { to: '/search',     label: 'Receipt Search',  icon: '🔍' },
  { to: '/customer',   label: 'Customer Lookup', icon: '👤' },
  { to: '/ai-search',  label: 'AI Search',       icon: '✨' },
  { to: '/audit',      label: 'Audit Log',       icon: '📋' },
]

export default function Layout() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="brand-name">Giant Eagle</div>
          <div className="brand-subtitle">CS Receipt Lookup</div>
        </div>
        <nav className="sidebar-nav">
          {navItems.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
            >
              <span className="nav-icon">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}

          {/* Divider */}
          <div style={{ borderTop: '1px solid rgba(255,255,255,0.15)', margin: '10px 0' }} />

          <NavLink
            to="/api-docs"
            className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
          >
            <span className="nav-icon">⚡</span>
            API Docs
          </NavLink>
        </nav>
        <div className="sidebar-footer">CS Portal v2.0</div>
      </aside>
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  )
}

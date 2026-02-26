export default function ApiDocs() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <iframe
        src="/docs"
        title="API Documentation"
        style={{
          flex: 1,
          width: '100%',
          border: 'none',
        }}
      />
    </div>
  )
}

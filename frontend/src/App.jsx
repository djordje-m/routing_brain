function App() {
  return (
    <div style={{ padding: '20px', fontFamily: 'sans-serif' }}>
      <h1>Routing Brain</h1>
      <p>
        API Base: <code>{import.meta.env.VITE_API_BASE}</code>
      </p>
      <p>
        WebSocket URL: <code>{import.meta.env.VITE_WS_URL}</code>
      </p>
    </div>
  )
}

export default App

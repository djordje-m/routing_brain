import { useEffect, useState } from 'react'
import { MapContainer, TileLayer, CircleMarker } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'

const CHICAGO_CENTER = [41.8781, -87.6298]
const STALE_COLOR = '#95a5a6'
const FRESH_COLOR = '#2ecc71'
const STALE_THRESHOLD_SECONDS = Number(
  import.meta.env.VITE_STALE_THRESHOLD_SECONDS
)

function App() {
  const [trucks, setTrucks] = useState({})
  // Bumped every 5s purely to force a re-render, so staleness is
  // recomputed against the current wall-clock time on every tick instead
  // of being computed once and cached.
  const [tick, setTick] = useState(0)

  useEffect(() => {
    const interval = setInterval(() => {
      setTick((t) => t + 1)
    }, 5000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    let ws
    let cancelled = false

    // Fetch the snapshot FIRST, then open the WebSocket only once it
    // resolves -- opening the socket before/concurrently with the fetch
    // could show a blank map until the next tick.
    fetch(`${import.meta.env.VITE_API_BASE}/trucks/latest`)
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return
        const initial = {}
        for (const truck of data) {
          initial[truck.truck_id] = truck
        }
        setTrucks(initial)

        ws = new WebSocket(import.meta.env.VITE_WS_URL)
        ws.onmessage = (event) => {
          const envelope = JSON.parse(event.data)
          if (envelope.type !== 'position_update') return
          const update = envelope.data
          setTrucks((prev) => ({
            ...prev,
            [update.truck_id]: {
              ...prev[update.truck_id],
              lat: update.lat,
              lon: update.lon,
              speed: update.speed,
              heading: update.heading,
              recorded_at: update.recorded_at,
            },
          }))
        }
      })
      .catch((err) => {
        console.error('Failed to load truck snapshot:', err)
      })

    return () => {
      cancelled = true
      if (ws) ws.close()
    }
  }, [])

  const nowSeconds = Date.now() / 1000

  return (
    <div style={{ height: '100vh', width: '100vw' }} data-tick={tick}>
      <MapContainer
        center={CHICAGO_CENTER}
        zoom={12}
        style={{ height: '100%', width: '100%' }}
      >
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        />
        {Object.values(trucks).map((truck) => {
          const recordedAtSeconds =
            new Date(truck.recorded_at).getTime() / 1000
          const isStale =
            nowSeconds - recordedAtSeconds > STALE_THRESHOLD_SECONDS
          return (
            <CircleMarker
              key={truck.truck_id}
              center={[truck.lat, truck.lon]}
              radius={8}
              fillColor={isStale ? STALE_COLOR : FRESH_COLOR}
              color={isStale ? STALE_COLOR : FRESH_COLOR}
              fillOpacity={0.9}
            />
          )
        })}
      </MapContainer>
    </div>
  )
}

export default App

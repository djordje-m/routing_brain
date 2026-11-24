import { useEffect, useState } from 'react'
import { MapContainer, TileLayer, CircleMarker } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'

const CHICAGO_CENTER = [41.8781, -87.6298]
const STALE_COLOR = '#95a5a6'
const FRESH_COLOR = '#2ecc71'
const OFF_SHIFT_COLOR = '#34495e'
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

    // Merges a freshly-fetched snapshot into state truck-by-truck, preserving
    // any fields the snapshot doesn't carry (e.g. a WS-delivered position
    // that arrived before this fetch resolved).
    const mergeSnapshot = (data) => {
      if (cancelled) return
      setTrucks((prev) => {
        const next = { ...prev }
        for (const truck of data) {
          next[truck.truck_id] = {
            ...(prev[truck.truck_id] ?? {}),
            ...truck,
          }
        }
        return next
      })
    }

    const fetchSnapshot = () =>
      fetch(`${import.meta.env.VITE_API_BASE}/trucks/latest`).then((res) =>
        res.json()
      )

    const openSocket = () => {
      if (cancelled || ws) return
      ws = new WebSocket(import.meta.env.VITE_WS_URL)
      ws.onopen = () => {
        // The WS only opens once the backend is actually reachable, so this
        // is a reliable second chance to pick up shift_start/shift_end --
        // which never arrive via position_update messages -- in case the
        // initial fetch ran before any truck had a position (cold boot) or
        // failed outright. Merge rather than replace so we don't lose
        // position fields a WS message may have already delivered.
        fetchSnapshot()
          .then((data) => {
            mergeSnapshot(data)
            // The WS can accept connections before the backend has any
            // stored positions (it doesn't wait on data existing), so this
            // fetch can still race the cold-boot window and come back
            // empty. One short retry covers that without adding real
            // complexity.
            if (!cancelled && data.length === 0) {
              setTimeout(() => {
                fetchSnapshot().then(mergeSnapshot).catch(() => {})
              }, 3000)
            }
          })
          .catch((err) => {
            console.error('Failed to refresh truck snapshot on WS open:', err)
          })
      }
      ws.onmessage = (event) => {
        const envelope = JSON.parse(event.data)
        if (envelope.type !== 'position_update') return
        const update = envelope.data
        setTrucks((prev) => ({
          ...prev,
          [update.truck_id]: {
            ...(prev[update.truck_id] ?? {}),
            truck_id: update.truck_id,
            lat: update.lat,
            lon: update.lon,
            speed: update.speed,
            heading: update.heading,
            recorded_at: update.recorded_at,
          },
        }))
      }
    }

    // Fetch the snapshot first and apply it to state before the WebSocket
    // opens, so the happy path still shows the full snapshot before any
    // live update lands. But the WS must open regardless of whether the
    // fetch succeeds -- a failed/rejected fetch (e.g. CORS misconfig,
    // network hiccup) must not block the live feed forever. `.finally()`
    // opens the socket after the fetch settles either way.
    fetch(`${import.meta.env.VITE_API_BASE}/trucks/latest`)
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return
        const initial = {}
        for (const truck of data) {
          initial[truck.truck_id] = truck
        }
        setTrucks(initial)
      })
      .catch((err) => {
        console.error('Failed to load truck snapshot:', err)
      })
      .finally(() => {
        openSocket()
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
          const isOffShift =
            truck.shift_start && truck.shift_end &&
            (nowSeconds < new Date(truck.shift_start).getTime() / 1000 ||
             nowSeconds > new Date(truck.shift_end).getTime() / 1000)
          const color = isOffShift ? OFF_SHIFT_COLOR : isStale ? STALE_COLOR : FRESH_COLOR
          return (
            <CircleMarker
              key={truck.truck_id}
              center={[truck.lat, truck.lon]}
              radius={8}
              fillColor={color}
              color={color}
              fillOpacity={0.9}
            />
          )
        })}
      </MapContainer>
    </div>
  )
}

export default App

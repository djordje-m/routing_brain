import { useEffect, useMemo, useState } from 'react'
import {
  MapContainer,
  TileLayer,
  CircleMarker,
  Marker,
  Popup,
  useMapEvents,
} from 'react-leaflet'
import 'leaflet/dist/leaflet.css'

const CHICAGO_CENTER = [41.8781, -87.6298]
const STALE_COLOR = '#95a5a6'
const FRESH_COLOR = '#2ecc71'
const OFF_SHIFT_COLOR = '#34495e'
const EN_ROUTE_COLOR = '#3498db'
const STALE_THRESHOLD_SECONDS = Number(
  import.meta.env.VITE_STALE_THRESHOLD_SECONDS
)
const TRUCK_TYPES = ['light', 'heavy', 'flatbed']

// Captures map clicks and reports lat/lng up to the parent via a callback,
// since useMapEvents must run inside a child of MapContainer.
function ClickCapture({ onMapClick }) {
  useMapEvents({
    click(e) {
      onMapClick(e.latlng)
    },
  })
  return null
}

function App() {
  const [trucks, setTrucks] = useState({})
  // Bumped every 5s purely to force a re-render, so staleness is
  // recomputed against the current wall-clock time on every tick instead
  // of being computed once and cached.
  const [tick, setTick] = useState(0)

  // A pending (not-yet-created, or created-but-not-confirmed) tow call the
  // operator is working through via the map-click popup. Shape:
  // { lat, lng, truckType, towCallId, candidates, error }
  // - towCallId/candidates are null until "Create Call" succeeds.
  // - error holds the brief inline message shown after a 409.
  const [pendingCall, setPendingCall] = useState(null)

  // Tow calls that have been confirmed (assigned to a truck) but not yet
  // marked complete, tracked purely client-side from create/confirm
  // responses -- the brief doesn't require a backend endpoint to list
  // these. Keyed by tow_call_id.
  const [activeCalls, setActiveCalls] = useState({})

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

  // Stable [lat, lng] for the current pending call. Memoized for two
  // reasons:
  // 1. react-leaflet's Marker/Popup only re-apply a `position` change when
  //    the prop changes *by reference*
  //    (@react-leaflet/core's updateMarker does
  //    `props.position !== prevProps.position`) -- an inline
  //    `[pendingCall.lat, pendingCall.lng]` array literal is a *new* array
  //    on every single re-render of App (every 5s tick, every WS
  //    position_update for any of the 20 trucks), which would cause
  //    needless setLatLng() churn on every one of those re-renders even
  //    though the coordinates never actually changed.
  // 2. It's also exactly the Popup's own `position` prop (see below), and
  //    @react-leaflet/core's usePopupLifecycle keys its open/bind effect
  //    on that same prop -- a stable reference means that effect only
  //    re-runs on a genuinely new pending call, not on unrelated re-renders.
  const pendingPosition = useMemo(
    () => (pendingCall ? [pendingCall.lat, pendingCall.lng] : null),
    [pendingCall?.lat, pendingCall?.lng]
  )

  const nowSeconds = Date.now() / 1000

  const handleMapClick = (latlng) => {
    // Only one pending call at a time -- a click while one is already open
    // is ignored rather than clobbering it, since the existing popup is
    // still anchored to a Marker on screen.
    if (pendingCall) return
    setPendingCall({
      lat: latlng.lat,
      lng: latlng.lng,
      truckType: TRUCK_TYPES[0],
      towCallId: null,
      candidates: null,
      error: null,
    })
  }

  const handleCreateCall = () => {
    if (!pendingCall) return
    fetch(`${import.meta.env.VITE_API_BASE}/tow-calls`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        lat: pendingCall.lat,
        lon: pendingCall.lng,
        required_truck_type: pendingCall.truckType,
      }),
    })
      .then(async (res) => {
        const data = await res.json()
        if (!res.ok) {
          // A non-2xx body won't have a `candidates` array, and the render
          // branch below only checks `candidates === null` to decide
          // whether to show the type-selector vs. the candidate list --
          // an `undefined` candidates array would otherwise fall into the
          // candidate-list branch and crash calling .length/.map on it.
          // Keep `candidates` null so it stays on the selector view, and
          // surface the failure inline instead.
          setPendingCall((prev) =>
            prev
              ? { ...prev, error: 'Failed to create call -- try again.' }
              : prev
          )
          return
        }
        setPendingCall((prev) =>
          prev
            ? {
                ...prev,
                towCallId: data.tow_call_id,
                candidates: data.candidates,
                error: null,
              }
            : prev
        )
      })
      .catch((err) => {
        console.error('Failed to create tow call:', err)
        setPendingCall((prev) =>
          prev ? { ...prev, error: 'Failed to create call -- try again.' } : prev
        )
      })
  }

  const handleConfirmCandidate = (truckId) => {
    if (!pendingCall || !pendingCall.towCallId) return
    const towCallId = pendingCall.towCallId
    fetch(`${import.meta.env.VITE_API_BASE}/tow-calls/${towCallId}/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ truck_id: truckId }),
    })
      .then(async (res) => {
        const body = await res.json()
        if (res.status === 409) {
          // Stale candidate list -- replace it with the fresh one the
          // backend just computed and let the operator pick again, rather
          // than silently failing or closing the popup.
          setPendingCall((prev) =>
            prev
              ? {
                  ...prev,
                  candidates: body.candidates,
                  error: 'That truck became unavailable -- pick another.',
                }
              : prev
          )
          return
        }
        if (!res.ok) {
          throw new Error(`Unexpected confirm response: ${res.status}`)
        }
        // Assigned. The truck's own active_call_id will update through the
        // normal snapshot/WS path, but we optimistically set it here too so
        // the marker turns en_route immediately instead of waiting on the
        // next WS refresh.
        setTrucks((prev) => ({
          ...prev,
          [truckId]: {
            ...(prev[truckId] ?? {}),
            active_call_id: towCallId,
          },
        }))
        setActiveCalls((prev) => ({
          ...prev,
          [towCallId]: {
            tow_call_id: towCallId,
            lat: pendingCall.lat,
            lon: pendingCall.lng,
            truck_id: truckId,
            truck_name:
              pendingCall.candidates?.find((c) => c.truck_id === truckId)
                ?.name ?? `Truck ${truckId}`,
            eta_seconds: body.eta_seconds,
          },
        }))
        setPendingCall(null)
      })
      .catch((err) => {
        console.error('Failed to confirm tow call:', err)
        setPendingCall((prev) =>
          prev ? { ...prev, error: 'Failed to confirm -- try again.' } : prev
        )
      })
  }

  const handleCompleteCall = (towCallId) => {
    fetch(`${import.meta.env.VITE_API_BASE}/tow-calls/${towCallId}/complete`, {
      method: 'POST',
    })
      .then((res) => {
        if (!res.ok) throw new Error(`Unexpected complete response: ${res.status}`)
        setActiveCalls((prev) => {
          const next = { ...prev }
          delete next[towCallId]
          return next
        })
      })
      .catch((err) => {
        console.error('Failed to complete tow call:', err)
      })
  }

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
          const isEnRoute = truck.active_call_id != null
          const color = isOffShift
            ? OFF_SHIFT_COLOR
            : isEnRoute
            ? EN_ROUTE_COLOR
            : isStale
            ? STALE_COLOR
            : FRESH_COLOR
          return (
            <CircleMarker
              key={truck.truck_id}
              center={[truck.lat, truck.lon]}
              radius={8}
              // react-leaflet v4's CircleMarker only re-applies style
              // changes via the dedicated `pathOptions` prop (see
              // @react-leaflet/core's usePathOptions) -- passing
              // fillColor/color/fillOpacity directly only sets the
              // *initial* style at creation and is never re-applied on
              // re-render, which would silently freeze every marker's
              // color at whatever it was on first mount (e.g. never
              // turning blue on confirm, never clearing off-shift/stale).
              pathOptions={{ color, fillColor: color, fillOpacity: 0.9 }}
              className={`truck-marker truck-marker-${truck.truck_id}`}
            />
          )
        })}

        <ClickCapture onMapClick={handleMapClick} />

        {pendingCall && pendingPosition && (
          <>
            {/* Plain pin for the pending call's location. Deliberately NOT
                wrapping a <Popup> as its child: @react-leaflet/core's
                usePopupLifecycle only *opens* a marker-nested popup via an
                imperative openPopup() call from outside (Popup-as-child
                just binds on mount, it doesn't auto-open) -- and under
                React 18 StrictMode's dev-only double-invoke-effects-on-mount
                behavior (which the actual `npm run dev` deployment runs
                under), a parent-level effect calling openPopup() only
                fires once, sandwiched *before* the child Popup's own
                mount->cleanup->remount dance finishes, leaving the popup
                freshly re-bound but never reopened. Rendering Popup
                directly under MapContainer instead sidesteps this: its own
                lifecycle effect calls instance.openOn(map) directly, so it
                reliably ends up open regardless of how many times
                StrictMode re-invokes that same effect. */}
            <Marker position={pendingPosition} />
            <Popup
              position={pendingPosition}
              autoClose={false}
              closeOnClick={false}
              // Leaflet's own close button is disabled in favor of a plain
              // React button below that calls setPendingCall(null)
              // directly. react-leaflet has no `onClose` prop (Popup's
              // props are just PopupOptions + eventHandlers), and the
              // Leaflet-level event that corresponds to "closed" is fired
              // on the *popup instance* as 'remove' (not 'popupclose' --
              // that name is a map-level event) -- which also fires as
              // part of React 18 StrictMode's dev-only
              // mount->cleanup->remount dance on this popup's first mount,
              // so wiring through it risked resetting pendingCall back to
              // null moments after a brand-new pending call was created.
              // A real onClick handler in our own JSX has no such
              // ambiguity.
              closeButton={false}
            >
              <div>
                {/* Literal "×" (U+00D7), not the HTML entity "&times;" --
                    JSX text children are not parsed as HTML, so an entity
                    reference would render as the literal 6-character
                    string "&times;" instead of being decoded. */}
                <button
                  onClick={(e) => {
                    // Without stopping propagation, this click also
                    // bubbles through to the map's own click handling
                    // (ClickCapture) -- since that handler's "ignore while
                    // a pending call is open" guard reads `pendingCall`
                    // from its own render's closure, it still sees the
                    // *old* (non-null) value for this same click and would
                    // otherwise immediately open a brand-new pending call
                    // at this popup's screen location right after we just
                    // cleared it.
                    e.stopPropagation()
                    setPendingCall(null)
                  }}
                  style={{ float: 'right' }}
                  aria-label="Close"
                >
                  ×
                </button>
              </div>
              {pendingCall.candidates === null ? (
                <div>
                  <div>
                    <label>
                      Truck type:{' '}
                      <select
                        value={pendingCall.truckType}
                        onChange={(e) =>
                          setPendingCall((prev) =>
                            prev ? { ...prev, truckType: e.target.value } : prev
                          )
                        }
                      >
                        {TRUCK_TYPES.map((t) => (
                          <option key={t} value={t}>
                            {t}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <button onClick={handleCreateCall}>Create Call</button>
                  {pendingCall.error && (
                    <div style={{ color: 'red' }}>{pendingCall.error}</div>
                  )}
                </div>
              ) : (
                <div>
                  <div>
                    <strong>Candidates</strong>
                  </div>
                  {pendingCall.candidates.length === 0 && (
                    <div>No eligible trucks available.</div>
                  )}
                  {pendingCall.candidates.map((c) => (
                    <div key={c.truck_id}>
                      {c.name} ({Math.round(c.eta_seconds / 60)} min){' '}
                      <button onClick={() => handleConfirmCandidate(c.truck_id)}>
                        Confirm
                      </button>
                    </div>
                  ))}
                  {pendingCall.error && (
                    <div style={{ color: 'red' }}>{pendingCall.error}</div>
                  )}
                </div>
              )}
            </Popup>
          </>
        )}

        {Object.values(activeCalls).map((call) => (
          <CircleMarker
            key={call.tow_call_id}
            center={[call.lat, call.lon]}
            radius={5}
            fillColor="#f1c40f"
            color="#f1c40f"
            fillOpacity={1}
          >
            <Popup>
              <div>
                <div>{call.truck_name}</div>
                <div>ETA: {Math.round(call.eta_seconds / 60)} min</div>
                <button onClick={() => handleCompleteCall(call.tow_call_id)}>
                  Mark Complete
                </button>
              </div>
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  )
}

export default App

import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  CircleMarker,
  MapContainer,
  Marker,
  Polyline,
  Popup,
  TileLayer,
  Tooltip,
  useMap,
} from 'react-leaflet';
import L from 'leaflet';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

function toLocalInputValue(date) {
  const offset = date.getTimezoneOffset();
  const local = new Date(date.getTime() - offset * 60_000);
  return local.toISOString().slice(0, 16);
}

const initialForm = {
  current_location: 'Chicago, IL',
  pickup_location: 'Indianapolis, IN',
  dropoff_location: 'Dallas, TX',
  current_cycle_used: 12,
  start_at: toLocalInputValue(new Date()),
};

const statusMeta = {
  offDuty: { label: 'Off Duty', y: 193, className: 'off-duty' },
  sleeper: { label: 'Sleeper', y: 210, className: 'sleeper' },
  driving: { label: 'Driving', y: 228, className: 'driving' },
  onDuty: { label: 'On Duty', y: 246, className: 'on-duty' },
};

const markerIcon = new L.Icon({
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  iconRetinaUrl:
    'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
  iconSize: [25, 41],
  iconAnchor: [12, 41],
});

function App() {
  const [form, setForm] = useState(initialForm);
  const [plan, setPlan] = useState(null);
  const [activeDay, setActiveDay] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function submitTrip(event) {
    event.preventDefault();
    setLoading(true);
    setError('');
    try {
      const response = await fetch(`${API_URL}/plan/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...form,
          current_cycle_used: Number(form.current_cycle_used),
        }),
      });
      const text = await response.text();
      let payload = {};
      if (text) {
        try {
          payload = JSON.parse(text);
        } catch {
          throw new Error(
            response.ok
              ? 'Server returned invalid JSON.'
              : `Server error (${response.status})`,
          );
        }
      }
      if (!response.ok) {
        throw new Error(
          payload.error || `Trip planning failed (${response.status})`,
        );
      }
      setPlan(payload);
      setActiveDay(0);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  }

  const mapStops = useMemo(
    () =>
      plan?.segments.filter((segment) => segment.status !== 'driving') || [],
    [plan],
  );
  const activeLog = plan?.log_days?.[activeDay];

  return (
    <main>
      <section className='hero'>
        <div className='hero-copy'>
          <span className='eyebrow'>FMCSA HOS Trip Planner</span>
          <h1>Route planning with logs built in.</h1>
          <p>
            Plan a property-carrying trip from current location to pickup and
            drop-off, then generate route stops, rests, fueling, and daily
            driver log sheets under the 70-hour/8-day HOS rules.
          </p>
        </div>
        <TripForm
          form={form}
          setForm={setForm}
          submitTrip={submitTrip}
          loading={loading}
        />
      </section>

      {error && <div className='alert'>{error}</div>}

      {plan ? (
        <>
          <SummaryStrip plan={plan} />
          <WarningsPanel warnings={plan.warnings || []} />
          <section className='workspace'>
            <RoutePanel plan={plan} stops={mapStops} />
            <InstructionPanel plan={plan} />
          </section>
          <section className='logs-section'>
            <div className='section-heading'>
              <div>
                <span className='eyebrow'>Daily log sheets</span>
                <h2>Generated driver logs</h2>
              </div>
              <div className='tabs' role='tablist' aria-label='Log days'>
                {plan.log_days.map((day, index) => (
                  <button
                    key={day.date}
                    className={index === activeDay ? 'active' : ''}
                    onClick={() => setActiveDay(index)}
                    type='button'>
                    Day {index + 1}
                  </button>
                ))}
              </div>
            </div>
            {activeLog && <LogSheet day={activeLog} />}
          </section>
        </>
      ) : (
        <EmptyState />
      )}
    </main>
  );
}

function TripForm({ form, setForm, submitTrip, loading }) {
  function updateField(event) {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
  }

  return (
    <form className='trip-form' onSubmit={submitTrip}>
      <label>
        <span>Current location</span>
        <input
          name='current_location'
          value={form.current_location}
          onChange={updateField}
          placeholder='Example: Chicago, IL'
          disabled={loading}
        />
      </label>
      <label>
        <span>Pickup location</span>
        <input
          name='pickup_location'
          value={form.pickup_location}
          onChange={updateField}
          placeholder='Example: Indianapolis, IN'
          disabled={loading}
        />
      </label>
      <label>
        <span>Drop-off location</span>
        <input
          name='dropoff_location'
          value={form.dropoff_location}
          onChange={updateField}
          placeholder='Example: Dallas, TX'
          disabled={loading}
        />
      </label>
      <div className='form-row'>
        <label>
          <span>Current cycle used</span>
          <input
            name='current_cycle_used'
            value={form.current_cycle_used}
            onChange={updateField}
            type='number'
            min='0'
            max='70'
            step='0.25'
          />
        </label>
        <label>
          <span>Start time</span>
          <input
            name='start_at'
            value={form.start_at}
            onChange={updateField}
            type='datetime-local'
          />
        </label>
      </div>
      <button className='primary-button' disabled={loading} type='submit'>
        <span aria-hidden='true'>-&gt;</span>
        {loading ? 'Planning...' : 'Plan trip'}
      </button>
    </form>
  );
}

function SummaryStrip({ plan }) {
  const timeZone = plan.inputs?.time_zone;
  const items = [
    ['Miles', `${plan.summary.total_miles.toLocaleString()} mi`],
    ['Drive time', `${plan.summary.drive_hours} hr`],
    ['Elapsed', `${plan.summary.elapsed_hours} hr`],
    ['Log sheets', plan.summary.log_days],
    ['Fuel stops', plan.summary.fuel_stops],
    ['Arrival', formatDateTime(plan.summary.arrival_at, timeZone)],
  ];

  return (
    <section className='summary-strip'>
      {items.map(([label, value]) => (
        <div key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
      ))}
    </section>
  );
}

function WarningsPanel({ warnings }) {
  if (!warnings.length) return null;
  return (
    <section className='warnings-panel' role='status' aria-label='Planning warnings'>
      {warnings.map((warning) => (
        <p key={warning}>{warning}</p>
      ))}
    </section>
  );
}

function RoutePanel({ plan, stops }) {
  const route = plan.route.geometry || [];
  const timeZone = plan.inputs?.time_zone;
  const bounds = useMemo(
    () => (route.length ? L.latLngBounds(route) : null),
    [route],
  );

  if (route.length < 2) {
    return (
      <section className='map-panel'>
        <div className='panel-heading'>
          <div>
            <span className='eyebrow'>Route map</span>
            <h2>Trip route and stops</h2>
          </div>
        </div>
        <p className='map-error'>
          No route geometry returned. Try planning again or check API
          connectivity.
        </p>
      </section>
    );
  }

  return (
    <section className='map-panel'>
      <div className='panel-heading'>
        <div>
          <span className='eyebrow'>Route map</span>
          <h2>Trip route and stops</h2>
        </div>
      </div>
      <div className='map-shell'>
        <MapContainer
          center={route[0] || [39.5, -98.35]}
          zoom={5}
          scrollWheelZoom
          className='map'>
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url='https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
          />
          {bounds && <FitBounds bounds={bounds} />}
          <Polyline
            positions={route}
            pathOptions={{ color: '#0f766e', weight: 5, opacity: 0.9 }}
          />
          {Object.entries(plan.route.places).map(([key, place]) => (
            <Marker
              key={key}
              position={[place.lat, place.lng]}
              icon={markerIcon}>
              <Popup>
                <strong>{titleCase(key)}</strong>
                <br />
                {place.label}
              </Popup>
            </Marker>
          ))}
          {stops.map((stop, index) => {
            const point = stop.start_point || stop.end_point;
            if (!point) return null;
            return (
              <CircleMarker
                key={`${stop.start}-${index}`}
                center={point}
                radius={7}
                pathOptions={{
                  color: '#f97316',
                  fillColor: '#f97316',
                  fillOpacity: 0.85,
                  weight: 2,
                }}>
                <Tooltip>
                  {stop.note} - {formatTime(stop.start, timeZone)}
                </Tooltip>
              </CircleMarker>
            );
          })}
        </MapContainer>
      </div>
    </section>
  );
}

function FitBounds({ bounds }) {
  const map = useMap();

  useEffect(() => {
    if (bounds && bounds.isValid()) {
      map.fitBounds(bounds, { padding: [28, 28] });
    }
  }, [bounds, map]);

  return null;
}

function InstructionPanel({ plan }) {
  const timeZone = plan.inputs?.time_zone;
  const visibleSegments = plan.segments.filter(
    (segment) => segment.status !== 'driving' || segment.miles > 0,
  );

  return (
    <section className='instructions'>
      <div className='panel-heading'>
        <div>
          <span className='eyebrow'>Route instructions</span>
          <h2>Stops, rests, and duty changes</h2>
        </div>
      </div>
      <ol className='timeline'>
        {visibleSegments.map((segment, index) => (
          <li key={`${segment.start}-${index}`}>
            <span
              className={`dot ${statusMeta[segment.status]?.className || ''}`}
            />
            <div>
              <div className='timeline-title'>
                <strong>{segment.note}</strong>
                <span>{formatDateTime(segment.start, timeZone)}</span>
              </div>
              <p>
                {statusMeta[segment.status]?.label} - {segment.location} -{' '}
                {segment.duration_hours} hr
                {segment.miles ? ` - ${segment.miles} mi` : ''}
              </p>
            </div>
          </li>
        ))}
      </ol>
      <details className='assumptions'>
        <summary>Planning assumptions</summary>
        <ul>
          {plan.assumptions.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </details>
    </section>
  );
}

function LogSheet({ day }) {
  return (
    <div className='sheet-frame'>
      <LogCanvas day={day} />
    </div>
  );
}

function LogCanvas({ day }) {
  const canvasRef = useRef(null);
  const total = Object.values(day.totals).reduce(
    (sum, value) => sum + value,
    0,
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const image = new Image();
    image.onload = () => {
      drawLogSheet(canvas, image, day, total);
    };
    image.src = '/blank-paper-log.png';
  }, [day, total]);

  return (
    <canvas
      ref={canvasRef}
      className='log-canvas'
      width='1026'
      height='1036'
      role='img'
      aria-label={`Driver daily log for ${day.date}`}
    />
  );
}

function drawLogSheet(canvas, image, day, total) {
  const width = 513;
  const height = 518;
  const scale = 2;
  const ctx = canvas.getContext('2d');

  canvas.width = width * scale;
  canvas.height = height * scale;
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(image, 0, 0, width, height);

  const date = new Date(`${day.date}T12:00:00`);
  const month = new Intl.DateTimeFormat(undefined, { month: 'short' }).format(
    date,
  );
  const dayOfMonth = new Intl.DateTimeFormat(undefined, {
    day: 'numeric',
  }).format(date);
  const year = new Intl.DateTimeFormat(undefined, { year: 'numeric' }).format(
    date,
  );

  drawText(ctx, month, 183, 18, { align: 'center', size: 6.2 });
  drawText(ctx, dayOfMonth, 229, 18, { align: 'center', size: 6.2 });
  drawText(ctx, year, 273, 18, { align: 'center', size: 6.2 });
  drawText(ctx, truncate(day.from, 22), 100, 44, { size: 6.8 });
  drawText(ctx, truncate(day.to, 22), 285, 44, { size: 6.8 });
  drawText(ctx, day.total_miles, 92, 78, { align: 'center', size: 6.8 });
  drawText(ctx, 'TRK-1024 / VAN-88', 135, 110, { align: 'center', size: 6.8 });
  drawText(ctx, truncate(day.carrier, 26), 347, 73, {
    align: 'center',
    size: 6.8,
  });
  drawText(ctx, truncate(day.main_office, 26), 347, 95, {
    align: 'center',
    size: 6.8,
  });
  drawText(ctx, truncate(day.home_terminal, 26), 347, 117, {
    align: 'center',
    size: 6.8,
  });

  drawDutyGraph(ctx, day.segments);

  drawText(
    ctx,
    `Load: ${truncate(day.pickup, 15)} to ${truncate(day.dropoff, 15)}`,
    26,
    350,
    { size: 4.8 },
  );
  drawText(ctx, 'General freight', 26, 410, { size: 4.8 });

  drawRemarks(ctx, day.remarks);

  drawText(ctx, formatHours(day.totals.offDuty), 481, 196, {
    align: 'center',
    size: 4.7,
    color: '#0f3f3a',
  });
  drawText(ctx, formatHours(day.totals.sleeper), 481, 213, {
    align: 'center',
    size: 4.7,
    color: '#0f3f3a',
  });
  drawText(ctx, formatHours(day.totals.driving), 481, 231, {
    align: 'center',
    size: 4.7,
    color: '#0f3f3a',
  });
  drawText(ctx, formatHours(day.totals.onDuty), 481, 249, {
    align: 'center',
    size: 4.7,
    color: '#0f3f3a',
  });
  drawText(ctx, formatHours(total), 481, 282, {
    align: 'center',
    size: 4.7,
    color: '#0f3f3a',
  });
}

function drawText(ctx, value, x, y, options = {}) {
  ctx.save();
  ctx.fillStyle = options.color || '#101615';
  ctx.font = `${options.weight || 700} ${options.size || 6}px Arial, Helvetica, sans-serif`;
  ctx.textAlign = options.align || 'left';
  ctx.textBaseline = 'alphabetic';
  ctx.fillText(String(value ?? ''), x, y);
  ctx.restore();
}

function drawDutyGraph(ctx, segments) {
  const x0 = 64;
  const width = 391;
  const yFor = (status) => statusMeta[status]?.y || statusMeta.offDuty.y;
  const xFor = (hour) => x0 + (Math.max(0, Math.min(24, hour)) / 24) * width;

  ctx.save();
  ctx.strokeStyle = '#cf3a32';
  ctx.lineWidth = 2.2;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  segments.forEach((segment, index) => {
    const y = yFor(segment.status);
    const xStart = xFor(segment.start_hour);
    const xEnd = xFor(segment.end_hour);
    ctx.moveTo(xStart, y);
    ctx.lineTo(xEnd, y);
    const next = segments[index + 1];
    if (next && next.status !== segment.status) {
      const nextY = yFor(next.status);
      ctx.moveTo(xEnd, y);
      ctx.lineTo(xEnd, nextY);
    }
  });
  ctx.stroke();
  ctx.restore();
}

function drawRemarks(ctx, remarks) {
  const columns = [
    { x: 26, maxChars: 58 },
    { x: 275, maxChars: 50 },
  ];
  const startY = 284;
  const lineHeight = 6;
  const maxRows = 6;
  const capacity = columns.length * maxRows;
  const visibleRemarks = remarks.slice(0, capacity);

  visibleRemarks.forEach((remark, index) => {
    const column = columns[Math.floor(index / maxRows)];
    const row = index % maxRows;
    drawText(
      ctx,
      truncate(
        `${formatRemarkTime(remark.time)}  ${remark.location} - ${remark.note}`,
        column.maxChars,
      ),
      column.x,
      startY + row * lineHeight,
      {
        size: 3.3,
        weight: 600,
      },
    );
  });

  if (remarks.length > capacity) {
    drawText(ctx, `+${remarks.length - capacity} more duty changes`, columns[1].x, startY + (maxRows - 1) * lineHeight, {
      size: 3.2,
      weight: 700,
      color: '#0f3f3a',
    });
  }
}

function EmptyState() {
  return (
    <section className='empty-state'>
      <div>
        <span className='eyebrow'>Ready for dispatch</span>
        <h2>
          Enter the trip details to generate the route and ELD-style logs.
        </h2>
      </div>
      <p>
        The planner will calculate driving blocks, required breaks, fuel stops,
        10-hour rests, cycle restarts when needed, and daily log totals.
      </p>
    </section>
  );
}

function formatDateTime(value, timeZone) {
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZone,
  }).format(new Date(value));
}

function formatDate(value) {
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(new Date(`${value}T12:00:00`));
}

function formatTime(value, timeZone) {
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
    timeZone,
  }).format(new Date(value));
}

function formatRemarkTime(value) {
  const [hourRaw, minuteRaw] = String(value).split(':');
  const hour = Number(hourRaw);
  const minute = Number(minuteRaw);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return value;
  const normalizedHour = hour % 12 || 12;
  return `${normalizedHour}:${String(minute).padStart(2, '0')}`;
}

function formatHours(value) {
  return Number(value).toFixed(value % 1 === 0 ? 0 : 2);
}

function truncate(value, maxLength) {
  const text = String(value ?? '');
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(0, maxLength - 3))}...`;
}

function titleCase(value) {
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default App;

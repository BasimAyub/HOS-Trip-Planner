# HOS Trip Planner

A full-stack Django + React app for planning property-carrying truck trips under FMCSA Hours of Service rules.

The app accepts:

- Current location
- Pickup location
- Drop-off location
- Current cycle used, in hours
- Start time

It produces:

- A route map with planned duty stops
- Route instructions for driving, pickup/drop-off, fuel, breaks, rests, and restarts
- Paper-style daily driver log sheets generated from the same duty-status timeline

## Tech Stack

- Backend: Django 5
- Frontend: React, Vite, Leaflet
- Routing: OSRM
- Geocoding and map data: OpenStreetMap / Nominatim
- Log sheet asset: `frontend/public/blank-paper-log.png`

## HOS Assumptions

- Property-carrying driver
- 70-hour / 8-day cycle
- No adverse driving condition exception
- 11-hour driving limit
- 14-hour driving window
- 30-minute break after 8 cumulative driving hours
- 10-hour rest resets the daily driving/window limits
- 34-hour restart clears the 70-hour cycle when needed
- Pickup and drop-off each take 1 hour on duty
- Fueling occurs at least once every 1,000 miles and takes 30 minutes on duty
- Current cycle used is treated as already-consumed cycle time because prior-day recap details are not provided by the assignment input

## Run Locally

Install backend dependencies from the project root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Install frontend dependencies:

```bash
cd frontend
npm install
```

Start the backend:

```bash
cd backend
../.venv/bin/python manage.py runserver 8000
```

Start the frontend in a second terminal:

```bash
cd frontend
npm run dev -- --port 5173
```

Open:

```text
http://localhost:5173
```

## Tests

Run backend checks and tests:

```bash
cd backend
../.venv/bin/python manage.py check
../.venv/bin/python manage.py test trips
```

Build the frontend:

```bash
cd frontend
npm run build
```

## Docker

Run the app with Docker Compose:

```bash
docker compose up --build
```

Open:

```text
http://localhost:5173
```

The compose setup runs:

- Django API on `http://localhost:8000`
- React frontend on `http://localhost:5173`

## Environment Variables

| Variable | Used By | Purpose | Default |
| --- | --- | --- | --- |
| `DJANGO_SECRET_KEY` | Backend | Django secret key | Development placeholder |
| `DJANGO_DEBUG` | Backend | Enables Django debug mode | `true` |
| `DJANGO_ALLOWED_HOSTS` | Backend | Comma-separated allowed hosts | `localhost,127.0.0.1` |
| `CORS_ALLOWED_ORIGINS` | Backend | Comma-separated frontend origins | `http://localhost:5173,http://127.0.0.1:5173` |
| `VITE_API_URL` | Frontend | Backend API base URL | `http://localhost:8000/api` |

## API

```text
GET  /api/health/
POST /api/plan/
```

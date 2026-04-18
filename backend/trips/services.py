from __future__ import annotations

import bisect
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


AVG_TRUCK_SPEED_MPH = 55
MAX_DRIVING_MINUTES = 11 * 60
MAX_DUTY_WINDOW_MINUTES = 14 * 60
MAX_CYCLE_MINUTES = 70 * 60
BREAK_AFTER_DRIVING_MINUTES = 8 * 60
THIRTY_MINUTES = 30
TEN_HOUR_REST_MINUTES = 10 * 60
RESTART_MINUTES = 34 * 60
FUEL_INTERVAL_MILES = 1000


KNOWN_PLACES = {
    "atlanta, ga": (33.749, -84.388),
    "chicago, il": (41.8781, -87.6298),
    "dallas, tx": (32.7767, -96.797),
    "denver, co": (39.7392, -104.9903),
    "detroit, mi": (42.3314, -83.0458),
    "houston, tx": (29.7604, -95.3698),
    "indianapolis, in": (39.7684, -86.1581),
    "jacksonville, fl": (30.3322, -81.6557),
    "kansas city, mo": (39.0997, -94.5786),
    "los angeles, ca": (34.0522, -118.2437),
    "memphis, tn": (35.1495, -90.049),
    "miami, fl": (25.7617, -80.1918),
    "nashville, tn": (36.1627, -86.7816),
    "new york, ny": (40.7128, -74.006),
    "phoenix, az": (33.4484, -112.074),
    "portland, or": (45.5152, -122.6784),
    "salt lake city, ut": (40.7608, -111.891),
    "san francisco, ca": (37.7749, -122.4194),
    "seattle, wa": (47.6062, -122.3321),
    "st. louis, mo": (38.627, -90.1994),
}

STATE_TIME_ZONES = {
    "AK": "America/Anchorage",
    "AL": "America/Chicago",
    "AR": "America/Chicago",
    "AZ": "America/Phoenix",
    "CA": "America/Los_Angeles",
    "CO": "America/Denver",
    "CT": "America/New_York",
    "DC": "America/New_York",
    "DE": "America/New_York",
    "FL": "America/New_York",
    "GA": "America/New_York",
    "HI": "Pacific/Honolulu",
    "IA": "America/Chicago",
    "ID": "America/Denver",
    "IL": "America/Chicago",
    "IN": "America/Indiana/Indianapolis",
    "KS": "America/Chicago",
    "KY": "America/New_York",
    "LA": "America/Chicago",
    "MA": "America/New_York",
    "MD": "America/New_York",
    "ME": "America/New_York",
    "MI": "America/New_York",
    "MN": "America/Chicago",
    "MO": "America/Chicago",
    "MS": "America/Chicago",
    "MT": "America/Denver",
    "NC": "America/New_York",
    "ND": "America/Chicago",
    "NE": "America/Chicago",
    "NH": "America/New_York",
    "NJ": "America/New_York",
    "NM": "America/Denver",
    "NV": "America/Los_Angeles",
    "NY": "America/New_York",
    "OH": "America/New_York",
    "OK": "America/Chicago",
    "OR": "America/Los_Angeles",
    "PA": "America/New_York",
    "RI": "America/New_York",
    "SC": "America/New_York",
    "SD": "America/Chicago",
    "TN": "America/Chicago",
    "TX": "America/Chicago",
    "UT": "America/Denver",
    "VA": "America/New_York",
    "VT": "America/New_York",
    "WA": "America/Los_Angeles",
    "WI": "America/Chicago",
    "WV": "America/New_York",
    "WY": "America/Denver",
}

_REVERSE_GEOCODE_CACHE: dict[tuple[float, float], str] = {}


@dataclass
class Place:
    query: str
    label: str
    lat: float
    lng: float
    source: str


def plan_trip(payload: dict[str, Any]) -> dict[str, Any]:
    inputs = normalize_inputs(payload)
    current = geocode(inputs["current_location"])
    pickup = geocode(inputs["pickup_location"])
    dropoff = geocode(inputs["dropoff_location"])
    home_timezone = infer_home_timezone(current)
    inputs["start_at"] = parse_start_at(inputs["start_at_raw"], home_timezone)
    inputs["time_zone"] = home_timezone.key

    route_to_pickup = route_between(current, pickup)
    route_to_dropoff = route_between(pickup, dropoff)
    geometry = stitch_geometry(route_to_pickup["geometry"], route_to_dropoff["geometry"])
    total_miles = route_to_pickup["distance_miles"] + route_to_dropoff["distance_miles"]

    planner = HosPlanner(
        start_at=inputs["start_at"],
        current_cycle_used_hours=inputs["current_cycle_used"],
        geometry=geometry,
        total_miles=total_miles,
    )
    planner.set_route_places(
        [
            (0, city_state_label(current)),
            (route_to_pickup["distance_miles"], city_state_label(pickup)),
            (total_miles, city_state_label(dropoff)),
        ]
    )
    planner.add_drive(
        route_to_pickup["distance_miles"],
        route_to_pickup["duration_seconds"],
        "Deadhead to pickup",
        city_state_label(current),
    )
    planner.add_service(60, pickup, "Pickup and loading", city_state_label(pickup))
    planner.add_drive(
        route_to_dropoff["distance_miles"],
        route_to_dropoff["duration_seconds"],
        "Loaded trip to drop-off",
        city_state_label(pickup),
    )
    planner.add_service(60, dropoff, "Drop-off and unloading", city_state_label(dropoff))
    planner.finish(dropoff)

    segments = planner.serialized_segments()
    log_days = build_log_days(segments, inputs, current, pickup, dropoff)
    route_source = "OSRM + OpenStreetMap"
    if route_to_pickup["source"] != "osrm" or route_to_dropoff["source"] != "osrm":
        route_source = "Fallback straight-line estimate"
        planner.warnings.append(
            "OSRM routing failed for at least one leg, so mileage, map geometry, and stop positions use a straight-line fallback estimate."
        )

    drive_hours = sum(segment["duration_hours"] for segment in segments if segment["status"] == "driving")
    stops = [
        segment
        for segment in segments
        if segment["status"] != "driving" and segment["note"] not in {"Pre-trip inspection", "Post-trip inspection"}
    ]

    return {
        "inputs": {
            "current_location": inputs["current_location"],
            "pickup_location": inputs["pickup_location"],
            "dropoff_location": inputs["dropoff_location"],
            "current_cycle_used": inputs["current_cycle_used"],
            "start_at": inputs["start_at"].isoformat(),
            "time_zone": inputs["time_zone"],
        },
        "assumptions": [
            "Property-carrying driver using a 70-hour/8-day cycle.",
            "Current cycle used is treated as already-consumed time because prior-day recap details were not provided.",
            "No adverse driving condition exception.",
            "Pickup and drop-off each take 1 hour on duty.",
            "Fueling is inserted at least once every 1,000 miles and takes 30 minutes on duty.",
            "A 34-hour restart clears the 70-hour rolling record when the planner cannot continue otherwise.",
        ],
        "route": {
            "source": route_source,
            "distance_miles": round(total_miles, 1),
            "estimated_drive_hours": round(drive_hours, 2),
            "geometry": geometry,
            "places": {
                "current": place_to_dict(current),
                "pickup": place_to_dict(pickup),
                "dropoff": place_to_dict(dropoff),
            },
        },
        "summary": {
            "total_miles": round(total_miles, 1),
            "drive_hours": round(drive_hours, 2),
            "elapsed_hours": round((planner.clock - inputs["start_at"]).total_seconds() / 3600, 2),
            "log_days": len(log_days),
            "fuel_stops": len([s for s in segments if s["note"] == "Fueling"]),
            "required_rests": len([s for s in segments if "rest" in s["note"].lower() or "restart" in s["note"].lower()]),
            "cycle_restarts": planner.restart_count,
            "arrival_at": planner.clock.isoformat(),
        },
        "segments": segments,
        "stops": stops,
        "log_days": log_days,
        "warnings": planner.warnings,
    }


def normalize_inputs(payload: dict[str, Any]) -> dict[str, Any]:
    required = ["current_location", "pickup_location", "dropoff_location"]
    missing = [key for key in required if not str(payload.get(key, "")).strip()]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    try:
        current_cycle_used = float(payload.get("current_cycle_used", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Current cycle used must be a number of hours.") from exc

    if current_cycle_used < 0:
        raise ValueError("Current cycle used cannot be negative.")

    return {
        "current_location": str(payload["current_location"]).strip(),
        "pickup_location": str(payload["pickup_location"]).strip(),
        "dropoff_location": str(payload["dropoff_location"]).strip(),
        "current_cycle_used": min(current_cycle_used, 70),
        "start_at_raw": str(payload.get("start_at", "")).strip(),
    }


def parse_start_at(start_raw: str, home_timezone: ZoneInfo) -> datetime:
    if start_raw:
        start_at = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
    else:
        start_at = datetime.now(home_timezone)
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=home_timezone)
    else:
        start_at = start_at.astimezone(home_timezone)
    return round_to_next_quarter(start_at)


def round_to_next_quarter(value: datetime) -> datetime:
    discard = timedelta(minutes=value.minute % 15, seconds=value.second, microseconds=value.microsecond)
    rounded = value - discard
    if discard:
        rounded += timedelta(minutes=15)
    return rounded


def geocode(query: str) -> Place:
    normalized = " ".join(query.lower().replace(".", "").split())
    fallback = KNOWN_PLACES.get(normalized)
    if fallback:
        return Place(query=query, label=query, lat=fallback[0], lng=fallback[1], source="known-place")

    url = f"https://nominatim.openstreetmap.org/search?q={quote(query)}&format=json&limit=1&countrycodes=us"
    try:
        data = fetch_json(url)
        if data:
            item = data[0]
            return Place(
                query=query,
                label=item.get("display_name", query),
                lat=float(item["lat"]),
                lng=float(item["lon"]),
                source="nominatim",
            )
    except Exception:
        pass

    if fallback:
        return Place(query=query, label=query, lat=fallback[0], lng=fallback[1], source="known-place")

    raise ValueError(f"Could not geocode '{query}'. Try 'City, ST' for a US location.")


def route_between(start: Place, end: Place) -> dict[str, Any]:
    url = (
        "https://router.project-osrm.org/route/v1/driving/"
        f"{start.lng},{start.lat};{end.lng},{end.lat}?overview=full&geometries=geojson"
    )
    try:
        data = fetch_json(url)
        route = data["routes"][0]
        coords = downsample_geometry([[lat, lng] for lng, lat in route["geometry"]["coordinates"]])
        distance_miles = meters_to_miles(route["distance"])
        duration_seconds = float(route.get("duration", (distance_miles / AVG_TRUCK_SPEED_MPH) * 3600))
        return {
            "source": "osrm",
            "distance_miles": distance_miles,
            "duration_seconds": duration_seconds,
            "geometry": coords,
        }
    except Exception:
        miles = haversine_miles(start.lat, start.lng, end.lat, end.lng) * 1.18
        duration_seconds = (miles / AVG_TRUCK_SPEED_MPH) * 3600
        return {
            "source": "fallback",
            "distance_miles": miles,
            "duration_seconds": duration_seconds,
            "geometry": [[start.lat, start.lng], [end.lat, end.lng]],
        }


def fetch_json(url: str) -> Any:
    request = Request(url, headers={"User-Agent": "hos-trip-planner/1.0"})
    with urlopen(request, timeout=12) as response:
        time.sleep(1)
        return json.loads(response.read().decode("utf-8"))


def stitch_geometry(first: list[list[float]], second: list[list[float]]) -> list[list[float]]:
    if not first:
        return second
    if not second:
        return first
    return first + second[1:]


def downsample_geometry(coords: list[list[float]], limit: int = 900) -> list[list[float]]:
    if len(coords) <= limit:
        return coords
    step = math.ceil(len(coords) / limit)
    sampled = coords[::step]
    if sampled[-1] != coords[-1]:
        sampled.append(coords[-1])
    return sampled


def place_to_dict(place: Place) -> dict[str, Any]:
    return {
        "query": place.query,
        "label": short_label(place.label),
        "lat": place.lat,
        "lng": place.lng,
        "source": place.source,
    }


def short_label(label: str) -> str:
    parts = [part.strip() for part in label.split(",")]
    if len(parts) >= 2:
        return ", ".join(parts[:2])
    return label


def city_state_label(place: Place) -> str:
    label = short_label(place.label)
    if "," in label:
        parts = [part.strip() for part in label.split(",") if part.strip()]
        if len(parts) >= 2:
            state = parts[1]
            state = state_abbr(state)
            return f"{parts[0]}, {state.upper()}"
    return place.query


def infer_home_timezone(place: Place) -> ZoneInfo:
    state = state_from_label(city_state_label(place))
    if state and state in STATE_TIME_ZONES:
        return ZoneInfo(STATE_TIME_ZONES[state])
    if place.lng <= -141:
        return ZoneInfo("America/Anchorage")
    if place.lng <= -114:
        return ZoneInfo("America/Los_Angeles")
    if place.lng <= -100:
        return ZoneInfo("America/Denver")
    if place.lng <= -85:
        return ZoneInfo("America/Chicago")
    return ZoneInfo("America/New_York")


def state_from_label(label: str) -> str | None:
    parts = [part.strip() for part in label.split(",") if part.strip()]
    if len(parts) < 2:
        return None
    state = state_abbr(parts[1])
    return state if len(state) == 2 and state.isalpha() else None


def reverse_geocode_city_state(lat: float, lng: float) -> str | None:
    key = (round(lat, 3), round(lng, 3))
    if key in _REVERSE_GEOCODE_CACHE:
        return _REVERSE_GEOCODE_CACHE[key]

    url = (
        "https://nominatim.openstreetmap.org/reverse?"
        f"lat={lat:.6f}&lon={lng:.6f}&format=json&zoom=10&addressdetails=1"
    )
    try:
        data = fetch_json(url)
        address = data.get("address", {})
        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("hamlet")
            or address.get("municipality")
            or address.get("county")
        )
        state = address.get("state")
        if city and state:
            label = f"{city}, {state_abbr(state)}"
            _REVERSE_GEOCODE_CACHE[key] = label
            return label
    except Exception:
        return None
    return None


STATE_ABBR = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}


def state_abbr(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) == 2 and cleaned.isalpha():
        return cleaned.upper()
    return STATE_ABBR.get(cleaned.lower(), cleaned[:2].upper())


def meters_to_miles(meters: float) -> float:
    return meters / 1609.344


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def build_geometry_cumulative_miles(geometry: list[list[float]]) -> list[float]:
    if not geometry:
        return [0.0]
    cum = [0.0]
    for i in range(1, len(geometry)):
        d = haversine_miles(geometry[i - 1][0], geometry[i - 1][1], geometry[i][0], geometry[i][1])
        cum.append(cum[-1] + d)
    return cum


def interpolate_geometry(
    geometry: list[list[float]],
    cumulative_miles: list[float],
    total_miles: float,
    target_mile: float,
) -> tuple[float, float]:
    if not geometry:
        return (39.5, -98.35)
    if len(geometry) == 1:
        return (geometry[0][0], geometry[0][1])
    path_len = cumulative_miles[-1] if cumulative_miles else 0.0
    if target_mile <= 0:
        return (geometry[0][0], geometry[0][1])
    if target_mile >= total_miles or path_len <= 0:
        return (geometry[-1][0], geometry[-1][1])

    along = (target_mile / total_miles) * path_len
    idx = bisect.bisect_right(cumulative_miles, along) - 1
    idx = max(0, min(idx, len(geometry) - 2))
    seg_start = cumulative_miles[idx]
    seg_end = cumulative_miles[idx + 1]
    seg_len = max(seg_end - seg_start, 1e-9)
    t = (along - seg_start) / seg_len
    lat = geometry[idx][0] + (geometry[idx + 1][0] - geometry[idx][0]) * t
    lng = geometry[idx][1] + (geometry[idx + 1][1] - geometry[idx][1]) * t
    return (lat, lng)


class HosPlanner:
    def __init__(
        self,
        start_at: datetime,
        current_cycle_used_hours: float,
        geometry: list[list[float]],
        total_miles: float,
    ):
        self.clock = start_at
        self.geometry = geometry
        self._geom_cum = build_geometry_cumulative_miles(geometry)
        self.total_miles = max(total_miles, 0.1)
        self.route_mile = 0.0
        self.next_fuel_mile = FUEL_INTERVAL_MILES
        self.route_places: list[tuple[float, str]] = []
        self.prior_cycle_used_minutes = int(round(current_cycle_used_hours * 60))
        self.daily_on_duty: dict[str, int] = {}
        self.duty_window_start: datetime | None = None
        self.day_drive_minutes = 0
        self.drive_since_break_minutes = 0
        self.segments: list[dict[str, Any]] = []
        self.restart_count = 0
        self.warnings: list[str] = []

        if current_cycle_used_hours >= 70:
            self.warnings.append("Current cycle used is at or above 70 hours, so a 34-hour restart is planned first.")

    def rolling_on_duty_minutes(self) -> int:
        # The assessment only supplies one recap value: Current Cycle Used (Hrs).
        # Without prior-day recap details, do not invent rolling recapture hours.
        return self.prior_cycle_used_minutes + sum(self.daily_on_duty.values())

    def _accumulate_on_duty_for_cycle(self, status: str, start: datetime, end: datetime) -> None:
        if status not in ("driving", "onDuty"):
            return
        cur = start
        while cur < end:
            day_end = datetime.combine(cur.date(), dt_time.min, tzinfo=cur.tzinfo) + timedelta(days=1)
            chunk_end = min(end, day_end)
            chunk_min = int((chunk_end - cur).total_seconds() // 60)
            if chunk_min > 0:
                key = cur.date().isoformat()
                self.daily_on_duty[key] = self.daily_on_duty.get(key, 0) + chunk_min
            cur = chunk_end

    def add_drive(self, distance_miles: float, leg_duration_seconds: float, note: str, location: str) -> None:
        remaining = distance_miles
        mph_effective = distance_miles / max(leg_duration_seconds / 3600.0, 1e-6)
        mph_effective = max(25.0, min(85.0, mph_effective))
        while remaining > 0.05:
            self.ensure_shift_started(location)

            if self.drive_since_break_minutes >= BREAK_AFTER_DRIVING_MINUTES:
                self.add_break(self.point_at_current_mile(), "30-minute break after 8 hours driving")

            available = self.drive_available_minutes()
            if available <= 0:
                self.rest_or_restart(self.current_route_label(location))
                continue

            minutes_for_remaining = math.ceil(remaining / mph_effective * 60)
            miles_until_fuel = self.next_fuel_mile - self.route_mile
            minutes_until_fuel = math.ceil(miles_until_fuel / mph_effective * 60) if miles_until_fuel > 0 else 0
            drive_minutes = min(available, minutes_for_remaining)
            if minutes_until_fuel > 0:
                drive_minutes = min(drive_minutes, minutes_until_fuel)

            if drive_minutes <= 0:
                self.add_service(THIRTY_MINUTES, self.place_at_mile(self.route_mile), "Fueling")
                self.next_fuel_mile += FUEL_INTERVAL_MILES
                continue

            driven_miles = min(remaining, drive_minutes / 60.0 * mph_effective)
            start_mile = self.route_mile
            end_mile = self.route_mile + driven_miles
            start_point = self.point_at_mile(start_mile)
            end_point = self.point_at_mile(end_mile)
            segment_location = self.current_route_label(location)
            self.add_segment(
                status="driving",
                minutes=drive_minutes,
                location=segment_location,
                note=note,
                miles=driven_miles,
                start_point=start_point,
                end_point=end_point,
                route_mile=start_mile,
            )
            self.day_drive_minutes += drive_minutes
            self.drive_since_break_minutes += drive_minutes
            self.route_mile = end_mile
            remaining -= driven_miles

            if self.route_mile + 0.05 >= self.next_fuel_mile and remaining > 0.05:
                self.add_service(THIRTY_MINUTES, self.place_at_mile(self.route_mile), "Fueling")
                self.next_fuel_mile += FUEL_INTERVAL_MILES

    def add_service(self, minutes: int, place: Place | dict[str, Any], note: str, location: str | None = None) -> None:
        location_name = location or (place.query if isinstance(place, Place) else place.get("label", "Route stop"))
        while True:
            self.ensure_shift_started(location_name)
            if self.remaining_cycle_minutes() < minutes:
                self.add_restart(location_name)
                continue
            if self.window_remaining_minutes() < minutes:
                self.add_ten_hour_rest(location_name)
                continue
            break

        point = [place.lat, place.lng] if isinstance(place, Place) else [place["lat"], place["lng"]]
        self.add_segment("onDuty", minutes, location_name, note, 0, point, point, self.route_mile)
        if minutes >= THIRTY_MINUTES:
            self.drive_since_break_minutes = 0

    def finish(self, dropoff: Place) -> None:
        if self.duty_window_start is not None and self.window_remaining_minutes() >= 15 and self.remaining_cycle_minutes() >= 15:
            self.add_segment(
                "onDuty",
                15,
                dropoff.query,
                "Post-trip inspection",
                0,
                [dropoff.lat, dropoff.lng],
                [dropoff.lat, dropoff.lng],
                self.route_mile,
            )

    def ensure_shift_started(self, location: str) -> None:
        if self.duty_window_start is not None:
            return
        if self.remaining_cycle_minutes() < 15:
            self.add_restart(location)
        self.duty_window_start = self.clock
        point = self.point_at_mile(self.route_mile)
        self.add_segment("onDuty", 15, location, "Pre-trip inspection", 0, point, point, self.route_mile)

    def rest_or_restart(self, location: str) -> None:
        win_rem = self.window_remaining_minutes()
        drive_rem = MAX_DRIVING_MINUTES - self.day_drive_minutes
        cyc_rem = self.remaining_cycle_minutes()
        if cyc_rem <= 0:
            self.add_restart(location)
            return
        if win_rem <= 0 or drive_rem <= 0:
            self.add_ten_hour_rest(location)
            return
        self.add_ten_hour_rest(location)

    def add_break(self, place: dict[str, Any], note: str) -> None:
        self.add_segment("offDuty", THIRTY_MINUTES, place["label"], note, 0, [place["lat"], place["lng"]], [place["lat"], place["lng"]], self.route_mile)
        self.drive_since_break_minutes = 0

    def add_ten_hour_rest(self, location: str) -> None:
        self.add_post_trip_if_possible(location)
        point = self.point_at_mile(self.route_mile)
        self.add_segment("sleeper", TEN_HOUR_REST_MINUTES, location, "10-hour rest", 0, point, point, self.route_mile)
        self.duty_window_start = None
        self.day_drive_minutes = 0
        self.drive_since_break_minutes = 0

    def add_restart(self, location: str) -> None:
        self.add_post_trip_if_possible(location)
        point = self.point_at_mile(self.route_mile)
        self.add_segment("offDuty", RESTART_MINUTES, location, "34-hour restart for 70-hour cycle", 0, point, point, self.route_mile)
        self.prior_cycle_used_minutes = 0
        self.daily_on_duty.clear()
        self.duty_window_start = None
        self.day_drive_minutes = 0
        self.drive_since_break_minutes = 0
        self.restart_count += 1

    def add_post_trip_if_possible(self, location: str) -> None:
        if self.duty_window_start is None:
            return
        if self.window_remaining_minutes() >= 15 and self.remaining_cycle_minutes() >= 15:
            point = self.point_at_mile(self.route_mile)
            self.add_segment("onDuty", 15, location, "Post-trip inspection", 0, point, point, self.route_mile)

    def drive_available_minutes(self) -> int:
        return max(
            0,
            min(
                MAX_DRIVING_MINUTES - self.day_drive_minutes,
                self.window_remaining_minutes(),
                self.remaining_cycle_minutes(),
                BREAK_AFTER_DRIVING_MINUTES - self.drive_since_break_minutes,
            ),
        )

    def window_remaining_minutes(self) -> int:
        if self.duty_window_start is None:
            return MAX_DUTY_WINDOW_MINUTES
        elapsed = int((self.clock - self.duty_window_start).total_seconds() // 60)
        return max(0, MAX_DUTY_WINDOW_MINUTES - elapsed)

    def remaining_cycle_minutes(self) -> int:
        return max(0, MAX_CYCLE_MINUTES - self.rolling_on_duty_minutes())

    def add_segment(
        self,
        status: str,
        minutes: int,
        location: str,
        note: str,
        miles: float,
        start_point: list[float],
        end_point: list[float],
        route_mile: float,
    ) -> None:
        start = self.clock
        end = self.clock + timedelta(minutes=minutes)
        self._accumulate_on_duty_for_cycle(status, start, end)
        self.clock = end
        self.segments.append(
            {
                "status": status,
                "start": start,
                "end": self.clock,
                "duration_minutes": minutes,
                "location": location,
                "note": note,
                "miles": miles,
                "start_point": start_point,
                "end_point": end_point,
                "route_mile": route_mile,
            }
        )

    def serialized_segments(self) -> list[dict[str, Any]]:
        output = []
        for segment in self.segments:
            output.append(
                {
                    **segment,
                    "start": segment["start"].isoformat(),
                    "end": segment["end"].isoformat(),
                    "duration_hours": round(segment["duration_minutes"] / 60, 2),
                    "miles": round(segment["miles"], 1),
                }
            )
        return output

    def point_at_current_mile(self) -> dict[str, Any]:
        return self.place_at_mile(self.route_mile)

    def place_at_mile(self, mile: float) -> dict[str, Any]:
        lat, lng = interpolate_geometry(self.geometry, self._geom_cum, self.total_miles, mile)
        return {"label": self.label_at_mile(mile), "lat": lat, "lng": lng}

    def point_at_mile(self, mile: float) -> list[float]:
        lat, lng = interpolate_geometry(self.geometry, self._geom_cum, self.total_miles, mile)
        return [lat, lng]

    def label_at_mile(self, mile: float) -> str:
        lat, lng = interpolate_geometry(self.geometry, self._geom_cum, self.total_miles, mile)
        reverse_label = reverse_geocode_city_state(lat, lng)
        if reverse_label:
            return reverse_label
        if not self.route_places:
            return f"Nearest city, ST (mile {round(mile)})"
        return min(self.route_places, key=lambda item: abs(item[0] - mile))[1]

    def set_route_places(self, places: list[tuple[float, str]]) -> None:
        self.route_places = places

    def current_route_label(self, fallback: str) -> str:
        label = self.label_at_mile(self.route_mile)
        return label if label else fallback


def build_log_days(
    segments: list[dict[str, Any]],
    inputs: dict[str, Any],
    current: Place,
    pickup: Place,
    dropoff: Place,
) -> list[dict[str, Any]]:
    if not segments:
        return []

    first = datetime.fromisoformat(segments[0]["start"])
    last = datetime.fromisoformat(segments[-1]["end"])
    day_start = datetime.combine(first.date(), dt_time.min, tzinfo=first.tzinfo)
    final_day = datetime.combine(last.date(), dt_time.min, tzinfo=last.tzinfo)

    days = []
    current_day = day_start
    previous_status = "offDuty"
    while current_day <= final_day:
        next_day = current_day + timedelta(days=1)
        day_segments: list[dict[str, Any]] = []
        totals_sec = {"offDuty": 0, "sleeper": 0, "driving": 0, "onDuty": 0}
        remarks = []
        cursor = current_day

        for segment in segments:
            seg_start = datetime.fromisoformat(segment["start"])
            seg_end = datetime.fromisoformat(segment["end"])
            if seg_end <= current_day or seg_start >= next_day:
                continue

            if cursor < max(seg_start, current_day):
                gap_end = max(seg_start, current_day)
                add_day_piece(day_segments, totals_sec, "offDuty", cursor, gap_end, "Off duty", "Off duty", 0, current_day, next_day)
                cursor = gap_end

            start = max(seg_start, current_day)
            end = min(seg_end, next_day)
            miles = segment["miles"] * ((end - start).total_seconds() / max((seg_end - seg_start).total_seconds(), 1))
            add_day_piece(
                day_segments,
                totals_sec,
                segment["status"],
                start,
                end,
                segment["location"],
                segment["note"],
                miles,
                current_day,
                next_day,
            )
            if segment["status"] != previous_status or start == seg_start:
                remarks.append(
                    {
                        "time": start.strftime("%H:%M"),
                        "location": segment["location"],
                        "note": segment["note"],
                    }
                )
            previous_status = segment["status"]
            cursor = end

        if cursor < next_day:
            add_day_piece(day_segments, totals_sec, "offDuty", cursor, next_day, "Off duty", "Off duty", 0, current_day, next_day)

        total_sec = sum(totals_sec.values())
        remainder = 86400 - total_sec
        if remainder > 0:
            add_day_piece(
                day_segments,
                totals_sec,
                "offDuty",
                next_day - timedelta(seconds=remainder),
                next_day,
                "Off duty",
                "Log total adjustment",
                0,
                current_day,
                next_day,
            )

        days.append(
            {
                "date": current_day.date().isoformat(),
                "from": current.query,
                "to": dropoff.query,
                "pickup": pickup.query,
                "dropoff": dropoff.query,
                "total_miles": round(sum(piece["miles"] for piece in day_segments if piece["status"] == "driving"), 1),
                "totals": {status: round(sec / 3600.0, 2) for status, sec in totals_sec.items()},
                "segments": day_segments,
                "remarks": remarks,
                "carrier": "Demo Carrier",
                "main_office": "United States",
                "home_terminal": current.query,
            }
        )
        current_day = next_day

    return days


def add_day_piece(
    day_segments: list[dict[str, Any]],
    totals_sec: dict[str, int],
    status: str,
    start: datetime,
    end: datetime,
    location: str,
    note: str,
    miles: float,
    day_start: datetime,
    day_end: datetime,
) -> None:
    sec = int(round((end - start).total_seconds()))
    if sec <= 0:
        return
    totals_sec[status] += sec
    rel_start = max(0.0, min(24.0, (start - day_start).total_seconds() / 3600.0))
    rel_end = max(0.0, min(24.0, (end - day_start).total_seconds() / 3600.0))
    day_segments.append(
        {
            "status": status,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "start_hour": rel_start,
            "end_hour": 24.0 if end >= day_end else rel_end,
            "duration_hours": round(sec / 3600.0, 4),
            "location": location,
            "note": note,
            "miles": round(miles, 1),
        }
    )

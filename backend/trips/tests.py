from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from .services import HosPlanner, Place, build_log_days, parse_start_at, plan_trip


class HosPlannerTests(SimpleTestCase):
    def test_start_time_is_interpreted_in_home_terminal_timezone(self):
        start = parse_start_at("2026-04-18T04:31", ZoneInfo("America/Chicago"))

        self.assertEqual(start.isoformat(), "2026-04-18T04:45:00-05:00")

    def test_on_duty_service_resets_thirty_minute_break_clock(self):
        start = datetime(2026, 4, 18, tzinfo=ZoneInfo("America/Chicago"))
        planner = HosPlanner(start, 0, [[40.0, -90.0], [40.0, -80.0]], 600)
        planner.set_route_places([(0, "Start, IL"), (600, "End, IN")])

        with patch("trips.services.reverse_geocode_city_state", return_value=None):
            planner.add_drive(480, 8 * 3600, "Driving", "Start, IL")
            planner.add_service(30, {"lat": 40.0, "lng": -85.0, "label": "Fuel City, IN"}, "Fueling")
            planner.add_drive(60, 3600, "Driving", "Fuel City, IN")

        self.assertNotIn("30-minute break after 8 hours driving", [segment["note"] for segment in planner.segments])

    def test_cycle_exhaustion_prefers_restart_without_extra_ten_hour_rest(self):
        start = datetime(2026, 4, 18, tzinfo=ZoneInfo("America/Chicago"))
        planner = HosPlanner(start, 56, [[40.0, -90.0], [40.0, -80.0]], 2000)
        planner.set_route_places([(0, "Start, IL"), (1000, "Middle, MO"), (2000, "End, OH")])

        with patch("trips.services.reverse_geocode_city_state", return_value=None):
            planner.add_drive(2000, (2000 / 60) * 3600, "Long drive", "Start, IL")

        notes = [segment["note"] for segment in planner.segments]
        adjacent_pairs = list(zip(notes, notes[1:]))
        self.assertNotIn(("10-hour rest", "34-hour restart for 70-hour cycle"), adjacent_pairs)

    def test_reverse_city_state_label_is_used_for_intermediate_stops(self):
        start = datetime(2026, 4, 18, tzinfo=ZoneInfo("America/Chicago"))
        planner = HosPlanner(start, 0, [[35.0, -90.0], [36.0, -89.0]], 100)

        with patch("trips.services.reverse_geocode_city_state", return_value="Memphis, TN"):
            place = planner.place_at_mile(50)

        self.assertEqual(place["label"], "Memphis, TN")

    def test_log_days_preserve_all_required_remarks(self):
        start = datetime(2026, 4, 18, tzinfo=ZoneInfo("America/Chicago"))
        planner = HosPlanner(start, 0, [[40.0, -90.0], [40.0, -80.0]], 100)
        planner.set_route_places([(0, "Start, IL"), (100, "End, IN")])

        with patch("trips.services.reverse_geocode_city_state", return_value=None):
            for index in range(12):
                planner.add_service(
                    30,
                    {"lat": 40.0, "lng": -90.0, "label": "Start, IL"},
                    f"Service {index + 1}",
                    "Start, IL",
                )

        current = Place("Start, IL", "Start, IL", 40.0, -90.0, "test")
        pickup = Place("Pickup, IL", "Pickup, IL", 40.0, -90.0, "test")
        dropoff = Place("End, IN", "End, IN", 40.0, -80.0, "test")
        days = build_log_days(planner.serialized_segments(), {"start_at": start}, current, pickup, dropoff)

        self.assertGreaterEqual(len(days[0]["remarks"]), 13)
        self.assertEqual(days[0]["remarks"][-1]["note"], "Service 12")

    def test_fallback_routing_is_exposed_as_warning(self):
        def fake_route_between(start, end):
            source = "fallback" if end.query == "Dallas, TX" else "osrm"
            return {
                "source": source,
                "distance_miles": 20,
                "duration_seconds": 1800,
                "geometry": [[start.lat, start.lng], [end.lat, end.lng]],
            }

        payload = {
            "current_location": "Chicago, IL",
            "pickup_location": "Indianapolis, IN",
            "dropoff_location": "Dallas, TX",
            "current_cycle_used": 0,
            "start_at": "2026-04-18T08:00",
        }

        with patch("trips.services.route_between", side_effect=fake_route_between), patch(
            "trips.services.reverse_geocode_city_state", return_value=None
        ):
            plan = plan_trip(payload)

        self.assertEqual(plan["route"]["source"], "Fallback straight-line estimate")
        self.assertTrue(any("straight-line fallback" in warning for warning in plan["warnings"]))

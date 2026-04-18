from django.urls import path

from .views import health, plan_trip

urlpatterns = [
    path("health/", health, name="health"),
    path("plan/", plan_trip, name="plan-trip"),
]

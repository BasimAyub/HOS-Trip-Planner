import json
import os
from json import JSONDecodeError

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .services import plan_trip as build_plan


def _cors_origin_allowed(origin: str | None) -> str:
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    allowed = {o.strip() for o in raw.split(",") if o.strip()}
    if origin and origin in allowed:
        return origin
    return "*"


class SimpleCorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        origin = request.META.get("HTTP_ORIGIN")
        allow = _cors_origin_allowed(origin)
        if request.method == "OPTIONS":
            response = JsonResponse({})
        else:
            response = self.get_response(request)
        response["Access-Control-Allow-Origin"] = allow
        response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        return response


@require_http_methods(["GET"])
def health(request):
    return JsonResponse({"ok": True})


@csrf_exempt
@require_http_methods(["POST", "OPTIONS"])
def plan_trip(request):
    if request.method == "OPTIONS":
        return JsonResponse({})
    try:
        raw = request.body.decode("utf-8").strip()
        if not raw:
            return JsonResponse({"error": "Request body is empty."}, status=400)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return JsonResponse({"error": "JSON body must be an object."}, status=400)
        return JsonResponse(build_plan(payload))
    except JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"error": f"Unable to plan trip: {exc}"}, status=500)

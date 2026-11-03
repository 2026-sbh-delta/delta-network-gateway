import logging
import os
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import path
from django.views.decorators.http import require_http_methods

from demo.notes.models import Note


@login_required
@require_http_methods(["GET", "POST"])
def home(request):
    if request.method == "POST":
        if Path(os.environ.get("APP_WRITE_FENCE", "/run/delta/write-paused")).exists():
            logging.getLogger("demo").warning("Write paused for migration cutover")
            return JsonResponse({"error": "이전 전환 중입니다. 잠시 후 다시 시도하세요."}, status=503, headers={"Retry-After": "2"})
        text = request.POST.get("text", "").strip()
        if not 1 <= len(text) <= 160:
            return JsonResponse({"error": "1~160자 메모를 입력하세요."}, status=400)
        Note.objects.create(owner=request.user, text=text)
        return redirect("/")
    return render(request, "home.html", {"notes": Note.objects.filter(owner=request.user).order_by("-id")[:20], "environment": os.environ.get("APP_ENVIRONMENT", "local"), "version": os.environ.get("APP_VERSION", "development")})


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok", "environment": os.environ.get("APP_ENVIRONMENT", "local")})
    except Exception:
        logging.getLogger("demo").error("Database readiness check failed")
        return JsonResponse({"status": "unavailable"}, status=503)


urlpatterns = [path("", home), path("health", health),
               path("login/", LoginView.as_view(template_name="login.html")),
               path("logout/", LogoutView.as_view())]

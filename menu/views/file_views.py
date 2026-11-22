"""
Menu › Menu file: save this outlet's menu as a file, or load one (22 Nov 2026).

Loading is two steps: Check reads the file and shows what it would add and
update (nothing changes), Load does it. Between the two the checked file
waits in the cache for 15 minutes under a random token tied to the person
who checked it, so nobody else can load it and a reload can't load it twice.
The rules of the file itself are in menu/services/menu_file.py.
"""
import json
import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.demo_restrictions import BLOCKED_MESSAGE, is_demo_trailer
from core.decorators import role_required, tenant_required
from menu.services import menu_file

WAIT = 15 * 60


def _key(request, token):
    return f"menufile:{request.user.pk}:{request.user.outlet_id}:{token}"


def _outlet_or_back(request):
    if request.user.outlet is None:
        messages.error(request, "A menu belongs to an outlet: sign in as the restaurant's owner or manager.")
        return None
    return request.user.outlet


@login_required
@tenant_required
@role_required("owner", "manager")
def menu_file_page(request):
    outlet = _outlet_or_back(request)
    if outlet is None:
        return redirect("/dashboard/")
    return render(request, "menu/menu_file.html", {"outlet": outlet})


@login_required
@tenant_required
@role_required("owner", "manager")
def menu_file_download(request):
    outlet = _outlet_or_back(request)
    if outlet is None:
        return redirect("/dashboard/")
    body = json.dumps(menu_file.export_menu(outlet), ensure_ascii=False, indent=1)
    response = HttpResponse(body.encode("utf-8"), content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{menu_file.export_filename(outlet)}"'
    return response


@login_required
@tenant_required
@role_required("owner", "manager")
@require_POST
def menu_file_check(request):
    outlet = _outlet_or_back(request)
    if outlet is None:
        return redirect("/dashboard/")
    if is_demo_trailer(request):
        messages.info(request, BLOCKED_MESSAGE)
        return redirect("menu_file")
    upload = request.FILES.get("menu_file")
    if upload is None:
        messages.error(request, "Choose a menu file first.")
        return redirect("menu_file")
    if upload.size > menu_file.MAX_BYTES:
        messages.error(request, "That file is too big to be a menu file (over 2 MB).")
        return redirect("menu_file")
    try:
        clean = menu_file.read_menu_file(upload.read(menu_file.MAX_BYTES + 1))
        plan = menu_file.plan_import(outlet, clean)
    except menu_file.MenuFileError as e:
        messages.error(request, str(e))
        return redirect("menu_file")
    token = secrets.token_urlsafe(16)
    cache.set(_key(request, token), clean, WAIT)
    return render(request, "menu/menu_file.html", {"outlet": outlet, "plan": plan, "token": token,
                                                   "file_name": upload.name[:120]})


@login_required
@tenant_required
@role_required("owner", "manager")
@require_POST
def menu_file_load(request):
    outlet = _outlet_or_back(request)
    if outlet is None:
        return redirect("/dashboard/")
    if is_demo_trailer(request):
        messages.info(request, BLOCKED_MESSAGE)
        return redirect("menu_file")
    token = request.POST.get("token", "")
    key = _key(request, token)
    clean = cache.get(key) if token else None
    if clean is None:
        messages.error(request, "That check has expired or was already loaded. Choose the file and check it again.")
        return redirect("menu_file")
    cache.delete(key)                          # once: a reload can't load it twice
    try:
        plan = menu_file.apply_import(outlet, clean)
    except menu_file.MenuFileError as e:
        messages.error(request, str(e))
        return redirect("menu_file")
    messages.success(request, f"Menu loaded: {plan.new['items']} new dishes, {plan.updated['items']} updated, "
                              f"{plan.new['categories']} new categories. Nothing was removed.")
    return redirect("/menu/")

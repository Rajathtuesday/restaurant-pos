"""The warnings a box shows at the top of every staff page (nothing in the cloud)."""
import shutil
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from box.edition import is_box

BACKUP_STALE = timedelta(hours=36)
DISK_WARN_FREE = 0.15


def warnings_now():
    from box import backup, license
    from box.models import BoxState
    state = BoxState.get()
    out = []
    lic = license.current()
    if lic.status in ("grace", "moved", "expired", "invalid", "wrong_box", "trial_over") or (
            lic.status == "trial" and (lic.days_left or 0) <= 5) or (
            lic.status == "valid" and (lic.days_left or 0) <= 15):
        out.append({"level": "red" if lic.blocks_new_orders else "amber", "text": lic.message})
    if state.clock_warning:
        out.append({"level": "red", "text": state.clock_warning})
    now = timezone.now()
    if state.last_backup_ok is False:
        out.append({"level": "red", "text": f"Last night's backup failed: {state.last_backup_error}"})
    elif state.setup_done_at and now - state.setup_done_at > BACKUP_STALE and (
            state.last_backup_at is None or now - state.last_backup_at > BACKUP_STALE):
        out.append({"level": "amber", "text": "No backup in the last day and a half. Check the backup drive is plugged in."})
    try:
        usage = shutil.disk_usage(str(backup.backup_dir()) if backup.backup_dir().exists() else "/")
        if usage.free / usage.total < DISK_WARN_FREE:
            out.append({"level": "amber", "text": f"The box's disk is nearly full ({usage.free // (1024 ** 3)} GB free)."})
    except OSError:
        pass
    return out


def box_status(request):
    if not is_box():
        return {}
    data = {"is_box": True, "warnings": []}
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        warnings = cache.get("box_warnings")
        if warnings is None:
            warnings = warnings_now()
            cache.set("box_warnings", warnings, 60)
        data["warnings"] = warnings
    return {"box": data}

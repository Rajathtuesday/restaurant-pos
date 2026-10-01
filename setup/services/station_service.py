# setup/services/station_service.py
from django.db import IntegrityError, transaction

from setup.models import KitchenStation


def get_default_station_for(tenant, outlet):
    """Resolve (or auto-create) the default kitchen station for a tenant/outlet.

    Kept separate from get_default_station(user) so callers that have no user
    — e.g. aggregator/webhook order ingestion, where the order arrives from
    Zomato/Swiggy with no logged-in staff member — can still resolve a station.

    An outlet has at most one default station (one_default_station_per_outlet).
    This used to look only for an active default and otherwise create one, so
    an outlet whose default had been switched off hit that constraint on every
    call, and the bill page failed. Now, in order: the active default; if the
    default is switched off, the first active station stands in (or the
    switched-off default, when nothing is active); with no default at all, the
    first active station becomes the default, or a "General" one is made.
    """
    stations = KitchenStation.objects.filter(tenant=tenant, outlet=outlet)
    station = stations.filter(is_default=True, is_active=True).first()
    if station:
        return station

    default = stations.filter(is_default=True).first()
    first_active = stations.filter(is_active=True).order_by("id").first()
    if default:
        return first_active or default

    try:
        with transaction.atomic():
            if first_active:
                first_active.is_default = True
                first_active.save(update_fields=["is_default"])
                return first_active
            return KitchenStation.objects.create(tenant=tenant, outlet=outlet, name="General", is_default=True)
    except IntegrityError:
        # Another request made a default at the same moment: use theirs.
        return stations.get(is_default=True)


def get_default_station(user):
    return get_default_station_for(user.tenant, user.outlet)
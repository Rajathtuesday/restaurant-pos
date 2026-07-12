# setup/services/station_service.py
from setup.models import KitchenStation


def get_default_station_for(tenant, outlet):
    """Resolve (or auto-create) the default kitchen station for a tenant/outlet.

    Kept separate from get_default_station(user) so callers that have no user
    — e.g. aggregator/webhook order ingestion, where the order arrives from
    Zomato/Swiggy with no logged-in staff member — can still resolve a station.
    """
    station = KitchenStation.objects.filter(
        tenant=tenant,
        outlet=outlet,
        is_default=True,
        is_active=True
    ).first()

    if station:
        return station

    # Auto-create default station
    return KitchenStation.objects.create(
        tenant=tenant,
        outlet=outlet,
        name="General",
        is_default=True
    )


def get_default_station(user):
    return get_default_station_for(user.tenant, user.outlet)
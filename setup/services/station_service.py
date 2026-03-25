# setup/services/station_service.py
from setup.models import KitchenStation


def get_default_station(user):

    station = KitchenStation.objects.filter(
        tenant=user.tenant,
        outlet=user.outlet,
        is_default=True,
        is_active=True
    ).first()

    if station:
        return station

    # Auto-create default station
    return KitchenStation.objects.create(
        tenant=user.tenant,
        outlet=user.outlet,
        name="General",
        is_default=True
    )
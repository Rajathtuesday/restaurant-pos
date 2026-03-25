# setup/models.py
from django.db import models
from django.db.models import UniqueConstraint, Q


class KitchenStation(models.Model):

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)

    name = models.CharField(max_length=100)

    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["tenant", "outlet"],
                condition=Q(is_default=True),
                name="one_default_station_per_outlet"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.outlet})"


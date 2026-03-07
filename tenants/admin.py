# tenants/admin.py
from django.contrib import admin
from .models import Tenant, Outlet

admin.site.register(Tenant)
admin.site.register(Outlet)
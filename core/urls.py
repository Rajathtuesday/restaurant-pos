"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
# core/urls.py

from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from django.views.defaults import page_not_found, server_error
from django.views.generic import TemplateView
from core import views

def custom_404(request, exception):
    from django.shortcuts import render
    return render(request, "404.html", status=404)

def custom_500(request):
    from django.shortcuts import render
    return render(request, "500.html", status=500)

from django.http import HttpResponse

def robots_txt(request):
    content = """User-agent: *
Allow: /
Disallow: /admin/
Disallow: /billing/
Disallow: /tables/
Disallow: /inventory/
Disallow: /menu/
Disallow: /dashboard/
Disallow: /reports/

Sitemap: https://rasova.net/sitemap.xml"""
    return HttpResponse(content, content_type='text/plain')

def sitemap_xml(request):
    content = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://rasova.net/</loc>
    <lastmod>2026-05-05</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>"""
    return HttpResponse(content, content_type='application/xml')

handler404 = "core.urls.custom_404"
handler500 = "core.urls.custom_500"

urlpatterns = [

    path('admin/', admin.site.urls),

    # default redirect — authenticated → dashboard, anonymous → login
    path('', views.landing, name='landing'),

    # Health check
    path('health/', views.health_check, name='health_check'),
    # Demo tenant switcher — DEBUG only, 404 in production
    path('demo/', views.demo_switch, name='demo_switch'),
    path('robots.txt', robots_txt),
    path('sitemap.xml', sitemap_xml),
    path('favicon.ico', lambda r: HttpResponse(status=204)),
    # PWA
    path('sw.js', views.serve_sw, name='service_worker'),
    path('manifest.json', TemplateView.as_view(template_name='manifest.json', content_type='application/manifest+json')),

    # apps
    path('', include('accounts.urls')),
    path('', include('orders.urls')),
    
    
    # menu module
    path('menu/', include('menu.urls')),

    # reports module
    path('reports/', include('reports.urls')),

    # inventory module
    path('inventory/', include('inventory.urls')),
    
    # setup module
    path('setup/', include('setup.urls')),

    # shifts module
    path('shifts/', include('shifts.urls')),

    # crm module
    path('crm/', include('crm.urls')),

    # agency module
    path('agency/', include('agency.urls')),

    # portal — Rasova internal ops panel
    path('portal/', include('portal.urls', namespace='portal')),
    # backward compat: /superuser/ → /portal/
    path('superuser/', lambda r: __import__('django.shortcuts', fromlist=['redirect']).redirect('/portal/')),
    path('superuser/tenant/<int:tenant_id>/', lambda r, tenant_id: __import__('django.shortcuts', fromlist=['redirect']).redirect(f'/portal/tenant/{tenant_id}/')),

    # notifications module
    path('', include('notifications.urls')),
]

from django.conf import settings
from django.conf.urls.static import static

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
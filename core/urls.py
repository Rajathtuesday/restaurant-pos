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


def custom_404(request, exception):
    from django.shortcuts import render
    return render(request, "errors/404.html", status=404)

def custom_500(request):
    from django.shortcuts import render
    return render(request, "errors/500.html", status=500)

handler404 = "core.urls.custom_404"
handler500 = "core.urls.custom_500"

urlpatterns = [

    path('admin/', admin.site.urls),

    # default redirect
    path('', lambda request: redirect('login')),

    # PWA
    path('sw.js', TemplateView.as_view(template_name='sw.js', content_type='application/javascript')),
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
]

from django.conf import settings
from django.conf.urls.static import static

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
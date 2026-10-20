from django.urls import path

from covers import views

urlpatterns = [
    path("", views.covers, name="covers"),
    path("redeem/<int:order_id>/", views.redeem, name="cover-redeem"),
]

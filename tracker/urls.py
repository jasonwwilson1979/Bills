from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('api/state/', views.api_state, name='api_state'),
]


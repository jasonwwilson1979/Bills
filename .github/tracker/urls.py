from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('api/state/', views.api_state, name='api_state'),
    path('api/sharing/', views.api_sharing, name='api_sharing'),
    path('api/sharing/select/', views.api_sharing_select, name='api_sharing_select'),
]

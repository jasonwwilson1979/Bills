"""
URL configuration for billflow project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
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
from django.contrib import admin
from django.urls import include, path

from tracker import views as tracker_views

urlpatterns = [
    path('admin/', admin.site.urls),
    # BillFlow's styled sign-in page. It is listed before the allauth include
    # so it wins over allauth's default login page; the Google OAuth
    # handshake itself is still handled by allauth below.
    path('accounts/login/', tracker_views.login_view, name='login_page'),
    path('accounts/', include('allauth.urls')),
    path('', include('tracker.urls')),
]

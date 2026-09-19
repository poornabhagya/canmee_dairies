from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from django.views.static import serve as static_serve
from canmee_dairies.deploy import deploy


def dashboard(request, *args, **kwargs):
    """Lazy import — avoids pulling reportlab on URLConf import for this route alone."""
    from reports.views import DashboardView

    return DashboardView.as_view()(request, *args, **kwargs)


urlpatterns = [
    path(
        "favicon.ico",
        RedirectView.as_view(url=f"{settings.STATIC_URL}img/logo.png", permanent=False),
    ),
    path('admin/', admin.site.urls),
    path('', dashboard, name='dashboard'),
    path('auth/', include('authentication.urls')),
    path('users/', include('user_management.urls')),
    path('masters/', include('masters.urls')),
    path('collections/', include('milk_collections.urls')),
    path('dispatch/', include('dispatch.urls')),
    path('reports/', include('reports.urls')),
    path('hrm/', include('hrm.urls', namespace='hrm')),
    path('attendance/', include('attendance.urls')),
    path('branches/', include('branches.urls')),
    path('stock/', include('stock_management.urls')),
    path('suppliers/', include('suppliers.urls')),
    path('loans/', include('farmer_loans.urls')),
    path('point-loans/', include('collection_point_loans.urls')),
    path('assets/', include('assets.urls')),  # includes asset-register-pdf
    # /deploy/ — disabled unless DJANGO_DEPLOY_ENABLED=1 on server (see settings.py)
    path('deploy/', deploy, name='deploy'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
elif not settings.MIDDLEWARE or "whitenoise.middleware.WhiteNoiseMiddleware" not in settings.MIDDLEWARE:
    # Fallback only when WhiteNoise is not installed/enabled.
    urlpatterns += [
        path(
            "static/<path:path>",
            static_serve,
            {"document_root": settings.STATIC_ROOT},
        ),
        path(
            "media/<path:path>",
            static_serve,
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]
    if settings.FORCE_SCRIPT_NAME:
        _prefix = settings.FORCE_SCRIPT_NAME.strip("/")
        urlpatterns += [
            re_path(
                rf"^{_prefix}/static/(?P<path>.*)$",
                static_serve,
                {"document_root": settings.STATIC_ROOT},
            ),
            re_path(
                rf"^{_prefix}/media/(?P<path>.*)$",
                static_serve,
                {"document_root": settings.MEDIA_ROOT},
            ),
        ]
else:
    # Media still needs a fallback on shared hosting without an Alias.
    urlpatterns += [
        path(
            "media/<path:path>",
            static_serve,
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]
    if settings.FORCE_SCRIPT_NAME:
        _prefix = settings.FORCE_SCRIPT_NAME.strip("/")
        urlpatterns += [
            re_path(
                rf"^{_prefix}/media/(?P<path>.*)$",
                static_serve,
                {"document_root": settings.MEDIA_ROOT},
            ),
        ]

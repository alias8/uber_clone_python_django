from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import URLPattern, URLResolver, include, path

urlpatterns: list[URLResolver | URLPattern] = [
    path("admin/", admin.site.urls),
    path("", include("rides.urls")),
]

# Serves django.contrib.admin's own CSS/JS (found via AppDirectoriesFinder, no collectstatic
# needed) in dev. Only needed because this project runs `daphne` directly rather than through
# `manage.py runserver` — runserver adds this automatically via staticfiles' own runserver
# command override, but that override never fires here since "daphne" (listed first in
# INSTALLED_APPS) provides its own competing runserver command that wins app-order resolution
# (see settings.py's INSTALLED_APPS comment) even when actually running via plain `daphne`, not
# `manage.py runserver`, this URL wiring is what's serving /static/ at all.
urlpatterns += staticfiles_urlpatterns()

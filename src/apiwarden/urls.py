"""Django URLConf.

    urlpatterns += [path("api-docs/", include("apiwarden.urls"))]

Everything below the mount point is handled by one view, so the portal serves
the same routes here as it does standalone — including its own static assets,
which means no collectstatic step.
"""

from __future__ import annotations

from django.urls import re_path

from .views import docs

app_name = "apiwarden"

urlpatterns = [
    re_path(r"^(?P<path>.*)$", docs, name="docs"),
]

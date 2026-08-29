"""Django app config.

Imported only by Django, via INSTALLED_APPS, so the package still imports on a
machine with no Django installed.
"""

from __future__ import annotations

from django.apps import AppConfig


class DocwardenConfig(AppConfig):
    name = "docwarden"
    label = "docwarden"
    verbose_name = "Docwarden"

    def ready(self) -> None:
        # Load the specs once at startup so the first request is not the one
        # that pays for it, and so a broken doc set shows up in the logs at
        # boot rather than the first time somebody opens the page.
        from .views import get_portal

        get_portal()

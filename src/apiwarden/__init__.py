"""Serve a directory of OpenAPI specs as a live docs portal.

This module imports nothing framework-specific. The Django integration lives in
apps.py / urls.py / views.py, which Django loads only via INSTALLED_APPS and
include(), so the package imports cleanly with or without Django installed.
"""

__version__ = "0.2.0"

default_app_config = "apiwarden.apps.ApiwardenConfig"

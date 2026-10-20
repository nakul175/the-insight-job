import os

import dj_database_url

from .test_settings import *

DATABASES = {"default": dj_database_url.parse(os.environ["DATABASE_URL"])}

# Bound unavailable test-database connections before per-transaction lock limits apply.
DATABASES["default"].setdefault("OPTIONS", {})["connect_timeout"] = 5

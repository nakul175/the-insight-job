import os

import dj_database_url

from .test_settings import *  # noqa: F403

DATABASES = {"default": dj_database_url.parse(os.environ["DATABASE_URL"])}

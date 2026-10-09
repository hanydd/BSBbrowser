# SPDX-License-Identifier: AGPL-3.0-or-later
"""Docker settings for a read-only snapshot behind transaction-pooled PgBouncer."""
from .docker import *  # noqa: F403

DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'] = True  # noqa: F405

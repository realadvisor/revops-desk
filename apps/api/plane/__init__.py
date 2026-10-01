# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import os

# RevOps Desk reuses the models without starting Plane's worker infrastructure.
if os.environ.get("REVOPS_DESK") != "1":
    from .celery import app as celery_app

    __all__ = ("celery_app",)

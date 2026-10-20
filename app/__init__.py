"""kurator – self-hosted, filtered frontend for YouTube subscriptions."""

import os
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("kurator")
except PackageNotFoundError:  # pragma: no cover - running from a plain source tree
    __version__ = "0.0.0"

GIT_SHA = os.environ.get("KURATOR_GIT_SHA", "")

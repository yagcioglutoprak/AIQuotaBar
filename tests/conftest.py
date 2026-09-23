"""Test setup: isolate HOME and install the fake macOS modules.

Runs before any test imports `aiquotabar`, so config/log/history paths all
land in a throwaway directory instead of the developer's real home.
"""

import os
import sys
import tempfile

_HOME = tempfile.mkdtemp(prefix="aiquotabar-test-home-")
os.environ["HOME"] = _HOME
os.makedirs(os.path.join(_HOME, "Downloads"), exist_ok=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests import fake_macos  # noqa: E402

fake_macos.install()

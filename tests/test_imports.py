"""Check startup when a long-running interpreter already has another config module."""

from pathlib import Path
import subprocess
import sys


def test_application_imports_with_stale_generic_config():
    script = """
import sys
from types import ModuleType

sys.modules['config'] = ModuleType('config')

import services.auth_service
import services.approval_service
import services.request_service
import ui.request_views
from mdm_config import APPROVAL_ROLES

assert 'finance' in APPROVAL_ROLES
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

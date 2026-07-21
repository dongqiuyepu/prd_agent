"""
pytest configuration and shared fixtures for loan calculator UI tests.

Fixtures provided:
  flask_app  (session) — starts the Flask app once for the whole test session
  app_url    (session) — the base URL string, used by Playwright page.goto()

Playwright's own fixtures (page, browser, browser_context) are provided
automatically by pytest-playwright — no manual setup needed.
"""

import subprocess
import time
import sys
import os
from pathlib import Path
import pytest
import requests

try:
    import tomllib  # Python >= 3.11
except ImportError:  # pragma: no cover
    tomllib = None

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_ui_config():
    if tomllib is None:
        return {}
    pyproject = ROOT_DIR / "pyproject.toml"
    if not pyproject.exists():
        return {}
    try:
        with open(pyproject, "rb") as f:
            return tomllib.load(f).get("tool", {}).get("ui_automation", {})
    except Exception:
        return {}


UI_CONFIG = _load_ui_config()

APP_PORT = UI_CONFIG.get("app_port", 5001)
APP_URL = UI_CONFIG.get("app_url", f"http://localhost:{APP_PORT}")
_app_dir = UI_CONFIG.get("app_dir", "loan_calculator_app")
APP_DIR = os.path.abspath(_app_dir if os.path.isabs(_app_dir) else os.path.join(ROOT_DIR, _app_dir))
START_TIMEOUT = UI_CONFIG.get("start_timeout", 30)
POLL_INTERVAL = UI_CONFIG.get("poll_interval", 0.5)


def _wait_for_app(url: str, timeout: int = START_TIMEOUT) -> bool:
    """Poll /health until the Flask app is ready or timeout is reached."""
    health_url = url.rstrip('/') + '/health'
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(health_url, timeout=3)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
    return False


@pytest.fixture(scope="session")
def flask_app():
    """
    Session-scoped fixture: starts the Flask loan calculator app once,
    yields the base URL, then tears it down after all tests complete.
    """
    proc = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=os.path.abspath(APP_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    ready = _wait_for_app(APP_URL)
    if not ready:
        proc.terminate()
        proc.wait()
        raise RuntimeError(
            f"Flask app did not start within timeout at {APP_URL}. "
            "Check that loan_calculator_app/app.py is runnable."
        )

    yield APP_URL

    proc.terminate()
    proc.wait()


@pytest.fixture(scope="session")
def app_url(flask_app: str) -> str:
    """Provide the base app URL string to Playwright tests."""
    return flask_app

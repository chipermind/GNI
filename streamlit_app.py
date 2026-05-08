"""
Streamlit Cloud entrypoint (repo root).
Runs the WhatsApp Connect app from apps/wa-qr-cloud-ui/app.py.
No secrets required. Use ?api_base_url=... or env API_BASE_URL to point to your backend.
"""
import sys
from pathlib import Path

_app_dir = Path(__file__).resolve().parent / "apps" / "wa-qr-cloud-ui"
if str(_app_dir) not in sys.path:
    sys.path.insert(0, str(_app_dir))

import app  # noqa: F401, E402  # runs the app (all st.* at module level)

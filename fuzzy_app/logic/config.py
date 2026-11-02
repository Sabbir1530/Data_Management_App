"""
logic/config.py
The Gemini API key now lives only in a local .env file next to the app
(there is no Settings tab anymore). Add a line like:

    GEMINI_API_KEY=your-key-here

to the .env file in the same folder as main.py, and restart the app.
"""
import os

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

# Fixed AI-matching settings (previously exposed in the UI / Settings tab).
AI_MODEL = "gemini-2.5-flash"
AI_BATCH_SIZE = 50
AI_MAX_CONCURRENT = 15


def load_api_key():
    if os.path.exists(ENV_PATH):
        try:
            # utf-8-sig transparently strips a UTF-8 BOM if present. Windows
            # Notepad often saves new text files with a BOM, which makes a
            # plain utf-8 read see an invisible "\ufeff" glued onto the
            # first line — so `line.startswith("GEMINI_API_KEY=")` silently
            # fails even though the file looks correct when you open it.
            with open(ENV_PATH, "r", encoding="utf-8-sig") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    if key.strip().upper() == "GEMINI_API_KEY":
                        return value.strip().strip('"').strip("'")
        except Exception:
            pass
    return os.environ.get("GEMINI_API_KEY", "")


def env_file_status():
    """Returns (exists, has_key) for diagnostics — used to give a more
    specific error than a blanket 'missing API key' message."""
    exists = os.path.exists(ENV_PATH)
    has_key = bool(load_api_key())
    return exists, has_key

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MACHINES_FILE = BASE_DIR / 'machines.json'
ENV_FILE = BASE_DIR / '.env'
DATABASE_FILE = BASE_DIR / 'haile_server.db'
ORIGINAL_ENV_KEYS = set(os.environ)
ENV_FILE_KEYS: set[str] = set()


def read_env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values

    for raw_line in ENV_FILE.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_env_file() -> None:
    global ENV_FILE_KEYS
    file_values = read_env_file()
    current_keys = set(file_values)

    for key in ENV_FILE_KEYS - current_keys:
        if key not in ORIGINAL_ENV_KEYS:
            os.environ.pop(key, None)

    for key, value in file_values.items():
        if key not in ORIGINAL_ENV_KEYS:
            os.environ[key] = value
    ENV_FILE_KEYS = current_keys


load_env_file()

DEFAULT_APP_VERSION = '2.5.9'
DEFAULT_APP_TYPE = '2'
DEFAULT_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
DEFAULT_TIMEOUT = float(os.getenv('REQUEST_TIMEOUT', '8'))
DEFAULT_RETRY = int(os.getenv('REQUEST_RETRY', '1'))
ORDER_DETAIL_SYNC_DELAY_MS = int(os.getenv('ORDER_DETAIL_SYNC_DELAY_MS', '800'))
SSL_VERIFY = os.getenv('SSL_VERIFY', 'false').lower() in {'1', 'true', 'yes', 'on'}
ALLOW_REMOTE = os.getenv('ALLOW_REMOTE', 'false').lower() in {'1', 'true', 'yes', 'on'}
HOST = '0.0.0.0' if ALLOW_REMOTE else '127.0.0.1'
PORT = int(os.getenv('PORT', '5000'))
SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'replace-me-in-production')
PROCESS_TTL_SECONDS = int(os.getenv('PROCESS_TTL_SECONDS', '3600'))
DEFAULT_LEAD_MINUTES = int(os.getenv('DEFAULT_LEAD_MINUTES', '60'))
SCHEDULER_INTERVAL_SECONDS = int(os.getenv('SCHEDULER_INTERVAL_SECONDS', '30'))
DEFAULT_LNG = float(os.getenv('DEFAULT_LNG', '113.999622'))
DEFAULT_LAT = float(os.getenv('DEFAULT_LAT', '22.596488'))
BASE_URL = 'https://yshz-user.haier-ioc.com'

if not SSL_VERIFY:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def load_machines():
    with MACHINES_FILE.open('r', encoding='utf-8') as f:
        data = json.load(f)
    return data


def get_haile_token() -> str:
    load_env_file()
    return os.getenv('HAILE_TOKEN', '').strip()


def get_pushplus_url() -> str:
    load_env_file()
    return os.getenv('PUSHPLUS_URL', '').strip()

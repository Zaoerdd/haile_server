import json
import os
from pathlib import Path
from typing import Any

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


def normalize_base_path(value: str | None) -> str:
    raw = (value or '').strip()
    if not raw or raw == '/':
        return ''
    return '/' + raw.strip('/')

DEFAULT_APP_VERSION = '2.5.9'
DEFAULT_APP_TYPE = '2'
DEFAULT_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
DEFAULT_TIMEOUT = float(os.getenv('REQUEST_TIMEOUT', '8'))
DEFAULT_RETRY = int(os.getenv('REQUEST_RETRY', '1'))
ORDER_DETAIL_SYNC_DELAY_MS = int(os.getenv('ORDER_DETAIL_SYNC_DELAY_MS', '50'))
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
BASE_PATH = normalize_base_path(os.getenv('BASE_PATH', ''))
BASE_URL = 'https://yshz-user.haier-ioc.com'

if not SSL_VERIFY:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _machine_store_payload(favorites: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        'version': 2,
        'favorites': favorites or [],
    }


def _clean_machine_value(value: Any) -> str:
    return str(value or '').strip()


def _normalize_machine_record(item: Any, fallback_label: str = '') -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None

    qr_code = _clean_machine_value(item.get('qrCode') or item.get('code'))
    if not qr_code:
        return None

    record = {
        'label': _clean_machine_value(item.get('label') or item.get('name') or fallback_label) or qr_code,
        'qrCode': qr_code,
        'goodsId': _clean_machine_value(item.get('goodsId') or item.get('id')),
        'shopId': _clean_machine_value(item.get('shopId')),
        'shopName': _clean_machine_value(item.get('shopName')),
        'categoryCode': _clean_machine_value(item.get('categoryCode')),
        'categoryName': _clean_machine_value(item.get('categoryName')),
        'addedAt': _clean_machine_value(item.get('addedAt')),
    }
    return record


def normalize_machine_store(data: Any) -> list[dict[str, str]]:
    if isinstance(data, dict) and isinstance(data.get('favorites'), list):
        raw_items = data.get('favorites') or []
    elif isinstance(data, list):
        raw_items = data
    elif isinstance(data, dict):
        raw_items = [{'label': label, 'qrCode': qr_code} for label, qr_code in data.items()]
    else:
        raw_items = []

    favorites: list[dict[str, str]] = []
    seen_qr_codes: set[str] = set()
    for raw_item in raw_items:
        record = _normalize_machine_record(raw_item)
        if not record:
            continue
        qr_code = record['qrCode']
        if qr_code in seen_qr_codes:
            continue
        seen_qr_codes.add(qr_code)
        favorites.append(record)
    return favorites


def load_machines() -> list[dict[str, str]]:
    if not MACHINES_FILE.exists():
        return []

    with MACHINES_FILE.open('r', encoding='utf-8') as f:
        data = json.load(f)
    return normalize_machine_store(data)


def save_machines(favorites: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    normalized = normalize_machine_store(_machine_store_payload(favorites or []))
    payload = _machine_store_payload(normalized)
    payload_text = f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
    temp_file = MACHINES_FILE.with_name(f'{MACHINES_FILE.name}.tmp')
    try:
        temp_file.write_text(payload_text, encoding='utf-8')
        temp_file.replace(MACHINES_FILE)
    except OSError:
        try:
            temp_file.unlink(missing_ok=True)
        except OSError:
            pass
        MACHINES_FILE.write_text(payload_text, encoding='utf-8')
    return normalized


def get_haile_token() -> str:
    load_env_file()
    return os.getenv('HAILE_TOKEN', '').strip()


def get_pushplus_url() -> str:
    load_env_file()
    return os.getenv('PUSHPLUS_URL', '').strip()

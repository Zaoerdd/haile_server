import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MACHINES_FILE = BASE_DIR / 'machines.json'

DEFAULT_APP_VERSION = '2.5.9'
DEFAULT_APP_TYPE = '2'
DEFAULT_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
DEFAULT_TIMEOUT = float(os.getenv('REQUEST_TIMEOUT', '8'))
DEFAULT_RETRY = int(os.getenv('REQUEST_RETRY', '1'))
SSL_VERIFY = os.getenv('SSL_VERIFY', 'false').lower() in {'1', 'true', 'yes', 'on'}
ALLOW_REMOTE = os.getenv('ALLOW_REMOTE', 'false').lower() in {'1', 'true', 'yes', 'on'}
HOST = '0.0.0.0' if ALLOW_REMOTE else '127.0.0.1'
PORT = int(os.getenv('PORT', '5000'))
SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'replace-me-in-production')
PROCESS_TTL_SECONDS = int(os.getenv('PROCESS_TTL_SECONDS', '3600'))
BASE_URL = 'https://yshz-user.haier-ioc.com'

if not SSL_VERIFY:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def load_machines():
    with MACHINES_FILE.open('r', encoding='utf-8') as f:
        data = json.load(f)
    return data

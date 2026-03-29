import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


if 'urllib3' not in sys.modules:
    sys.modules['urllib3'] = types.SimpleNamespace(
        disable_warnings=lambda *args, **kwargs: None,
        exceptions=types.SimpleNamespace(InsecureRequestWarning=Warning),
    )

if 'requests' not in sys.modules:
    class _DummyRequestException(Exception):
        pass

    class _DummyTimeout(_DummyRequestException):
        pass

    class _DummySession:
        def request(self, *args, **kwargs):
            raise AssertionError('network access should not occur in scan machine status tests')

    sys.modules['requests'] = types.SimpleNamespace(
        Session=_DummySession,
        Timeout=_DummyTimeout,
        RequestException=_DummyRequestException,
    )

if 'flask' not in sys.modules:
    class _DummyFlask:
        def __init__(self, name):
            self.name = name
            self.config = {}
            self.wsgi_app = lambda environ, start_response: None

        def route(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator

    sys.modules['flask'] = types.SimpleNamespace(
        Flask=_DummyFlask,
        jsonify=lambda payload=None, **kwargs: payload or kwargs,
        render_template=lambda *args, **kwargs: '',
        request=types.SimpleNamespace(headers={}, script_root='', args={}),
        url_for=lambda *args, **kwargs: '',
    )

if 'werkzeug.middleware.proxy_fix' not in sys.modules:
    class _DummyProxyFix:
        def __init__(self, app, **kwargs):
            self.app = app

        def __call__(self, environ, start_response):
            return self.app(environ, start_response)

    sys.modules['werkzeug.middleware.proxy_fix'] = types.SimpleNamespace(ProxyFix=_DummyProxyFix)


_APP_MODULE = None


def load_app_module():
    global _APP_MODULE
    if _APP_MODULE is None:
        with patch('services.scheduler.reservation_scheduler.start'), patch('services.scheduler.reservation_scheduler.update_interval'):
            _APP_MODULE = importlib.import_module('app')
    return _APP_MODULE


class ScanMachineStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_module = load_app_module()

    @patch('app.fetch_all_room_machines')
    @patch('app.fetch_laundry_rooms')
    @patch('app.load_machines')
    def test_find_scan_machine_status_returns_matched_machine(self, load_machines_mock, fetch_rooms_mock, fetch_room_machines_mock):
        load_machines_mock.return_value = {'Machine A': 'QR-001'}
        fetch_rooms_mock.return_value = {
            'ok': True,
            'data': {
                'items': [
                    {'id': 'room-1', 'name': 'Room One', 'address': 'Floor 3 East'},
                ],
            },
        }
        fetch_room_machines_mock.return_value = {
            'ok': True,
            'data': {
                'items': [
                    {
                        'id': 'goods-1',
                        'name': 'Machine A',
                        'floorCode': '3F',
                        'state': 2,
                        'stateDesc': 'Running',
                        'finishTime': '2026-03-29T08:15:00+08:00',
                        'enableReserve': True,
                    },
                ],
            },
        }

        result = self.app_module.find_scan_machine_status(MagicMock(), 'QR-001', lng=120.0, lat=30.0)

        self.assertTrue(result['ok'])
        self.assertTrue(result['matched'])
        self.assertEqual(result['room']['id'], 'room-1')
        self.assertEqual(result['machine']['goodsId'], 'goods-1')
        self.assertEqual(result['machine']['scanCode'], 'QR-001')
        self.assertEqual(result['machine']['statusLabel'], '运行中')
        self.assertEqual(result['machine']['finishTimeText'], '08:15')

    @patch('app.fetch_all_room_machines')
    @patch('app.fetch_laundry_rooms')
    @patch('app.load_machines')
    def test_find_scan_machine_status_returns_unmatched_when_no_machine_matches(self, load_machines_mock, fetch_rooms_mock, fetch_room_machines_mock):
        load_machines_mock.return_value = {'Machine A': 'QR-001'}
        fetch_rooms_mock.return_value = {
            'ok': True,
            'data': {
                'items': [
                    {'id': 'room-1', 'name': 'Room One', 'address': 'Floor 3 East'},
                ],
            },
        }
        fetch_room_machines_mock.return_value = {
            'ok': True,
            'data': {
                'items': [
                    {
                        'id': 'goods-2',
                        'name': 'Machine B',
                        'floorCode': '3F',
                        'state': 1,
                        'stateDesc': 'Idle',
                        'enableReserve': True,
                    },
                ],
            },
        }

        result = self.app_module.find_scan_machine_status(MagicMock(), 'QR-001', lng=120.0, lat=30.0)

        self.assertTrue(result['ok'])
        self.assertFalse(result['matched'])
        self.assertIsNone(result['room'])
        self.assertIsNone(result['machine'])


if __name__ == '__main__':
    unittest.main()

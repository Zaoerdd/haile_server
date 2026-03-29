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

    def test_fetch_all_room_machines_merges_multiple_categories(self):
        client = MagicMock()
        client.position_device.return_value = {
            'ok': True,
            'data': [
                {'categoryCode': '00', 'categoryName': 'Washer', 'total': 1, 'idleCount': 1},
                {'categoryCode': '02', 'categoryName': 'Dryer', 'total': 1, 'idleCount': 0},
            ],
        }
        client.device_detail_page.side_effect = [
            {
                'ok': True,
                'data': {
                    'total': 1,
                    'items': [{'id': 'washer-1', 'name': 'Machine A'}],
                },
            },
            {
                'ok': True,
                'data': {
                    'total': 1,
                    'items': [{'id': 'dryer-1', 'name': 'Dryer A'}],
                },
            },
        ]

        result = self.app_module.fetch_all_room_machines(client, position_id='room-1')

        self.assertTrue(result['ok'])
        self.assertEqual(result['data']['total'], 2)
        self.assertEqual(
            [item['categoryCode'] for item in result['data']['items']],
            ['00', '02'],
        )
        self.assertEqual(
            [item['categoryName'] for item in result['data']['items']],
            ['Washer', 'Dryer'],
        )

    def test_merge_machine_with_run_info_promotes_running_status(self):
        client = MagicMock()
        client.goods_last_run_info.return_value = {
            'ok': True,
            'data': {
                'runInfo': {
                    'state': 2,
                    'stateDesc': 'Running',
                    'finishTime': '2026-03-29T08:15:00+08:00',
                },
            },
        }
        machine = {
            'goodsId': 'goods-1',
            'categoryCode': '00',
            'state': 3,
            'stateDesc': 'Unavailable',
            'finishTime': '',
            'finishTimeText': '',
            'statusLabel': '不可用',
            'statusDetail': 'Unavailable',
            'enableReserve': True,
        }

        result = self.app_module.merge_machine_with_run_info(client, machine)

        self.assertEqual(result['statusLabel'], '运行中')
        self.assertEqual(result['finishTimeText'], '08:15')
        self.assertIn('预计完成', result['statusDetail'])

    @patch('app.load_machines')
    def test_normalize_machine_detail_uses_goods_details_code_and_marks_favorite(self, load_machines_mock):
        load_machines_mock.return_value = [
            {
                'label': 'Machine A',
                'qrCode': 'QR-001',
                'goodsId': 'goods-1',
                'shopId': 'room-1',
                'shopName': 'Room One',
            }
        ]

        result = self.app_module.normalize_machine_detail(
            {
                'id': 'goods-1',
                'name': 'Machine A',
                'code': 'QR-001',
                'categoryCode': '00',
                'categoryName': 'Washer',
                'shopId': 'room-1',
                'shopName': 'Room One',
                'enableReserve': True,
                'items': [{'id': 101, 'name': 'Quick', 'price': '3.00'}],
            }
        )

        self.assertTrue(result['supportsVirtualScan'])
        self.assertEqual(result['scanCode'], 'QR-001')
        self.assertTrue(result['isFavorite'])
        self.assertEqual(result['categoryName'], 'Washer')

    @patch('app.load_machines')
    def test_normalize_machine_detail_falls_back_to_saved_qr_code(self, load_machines_mock):
        load_machines_mock.return_value = [
            {
                'label': 'Machine A',
                'qrCode': 'QR-LEGACY',
                'goodsId': 'goods-1',
            }
        ]

        result = self.app_module.normalize_machine_detail(
            {
                'id': 'goods-1',
                'name': 'Machine A',
                'code': '',
                'categoryCode': '00',
                'items': [],
            }
        )

        self.assertTrue(result['supportsVirtualScan'])
        self.assertEqual(result['scanCode'], 'QR-LEGACY')
        self.assertTrue(result['isFavorite'])

    @patch('app.save_machines')
    @patch('app.load_machines')
    def test_upsert_scan_machine_replaces_existing_qr_code_entry(self, load_machines_mock, save_machines_mock):
        load_machines_mock.return_value = [
            {
                'label': 'Old Label',
                'qrCode': 'QR-001',
                'goodsId': 'goods-1',
                'addedAt': '2026-03-28T10:00:00+08:00',
            }
        ]
        save_machines_mock.side_effect = lambda favorites: favorites

        result = self.app_module.upsert_scan_machine(
            {
                'label': 'New Label',
                'qrCode': 'QR-001',
                'goodsId': 'goods-1',
                'shopId': 'room-9',
                'shopName': 'Room Nine',
            }
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['label'], 'New Label')
        self.assertEqual(result[0]['shopId'], 'room-9')
        self.assertEqual(result[0]['addedAt'], '2026-03-28T10:00:00+08:00')

    @patch('app.fetch_laundry_rooms')
    @patch('app.fetch_all_room_machines')
    @patch('app.load_machines')
    def test_find_scan_machine_status_prefers_targeted_favorite_lookup(self, load_machines_mock, fetch_room_machines_mock, fetch_rooms_mock):
        load_machines_mock.return_value = [
            {
                'label': 'Machine A',
                'qrCode': 'QR-001',
                'goodsId': 'goods-1',
                'shopId': 'room-9',
                'shopName': 'Room Nine',
            }
        ]
        fetch_room_machines_mock.return_value = {
            'ok': True,
            'data': {
                'items': [
                    {
                        'id': 'goods-1',
                        'name': 'Machine A',
                        'categoryCode': '00',
                        'state': 1,
                        'stateDesc': 'Idle',
                        'enableReserve': True,
                    },
                ],
            },
        }
        fetch_rooms_mock.return_value = {'ok': True, 'data': {'items': []}}

        client = MagicMock()
        client.position_detail.return_value = {
            'ok': True,
            'data': {'id': 'room-9', 'shopId': 'room-9', 'name': 'Room Nine', 'address': 'Floor 3'},
        }
        client.goods_last_run_info.return_value = {'ok': False, 'error_type': 'business', 'msg': 'ignored'}

        result = self.app_module.find_scan_machine_status(client, 'QR-001', lng=120.0, lat=30.0)

        self.assertTrue(result['ok'])
        self.assertTrue(result['matched'])
        self.assertEqual(result['room']['id'], 'room-9')
        self.assertEqual(result['machine']['goodsId'], 'goods-1')
        self.assertEqual(result['machine']['scanCode'], 'QR-001')
        fetch_rooms_mock.assert_not_called()

    @patch('app.fetch_all_room_machines')
    @patch('app.fetch_laundry_rooms')
    @patch('app.load_machines')
    def test_find_scan_machine_status_returns_unmatched_when_no_machine_matches(self, load_machines_mock, fetch_rooms_mock, fetch_room_machines_mock):
        load_machines_mock.return_value = [{'label': 'Machine A', 'qrCode': 'QR-001'}]
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
                        'categoryCode': '00',
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

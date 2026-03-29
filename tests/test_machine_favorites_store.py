import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


if 'urllib3' not in sys.modules:
    sys.modules['urllib3'] = types.SimpleNamespace(
        disable_warnings=lambda *args, **kwargs: None,
        exceptions=types.SimpleNamespace(InsecureRequestWarning=Warning),
    )


import config


class MachineFavoritesStoreTests(unittest.TestCase):
    def test_load_machines_supports_legacy_mapping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            machines_file = Path(temp_dir) / 'machines.json'
            machines_file.write_text(
                json.dumps({'Machine A': 'QR-001', 'Machine B': 'QR-002'}, ensure_ascii=False),
                encoding='utf-8',
            )
            with patch.object(config, 'MACHINES_FILE', machines_file):
                favorites = config.load_machines()

        self.assertEqual(
            favorites,
            [
                {
                    'label': 'Machine A',
                    'qrCode': 'QR-001',
                    'goodsId': '',
                    'shopId': '',
                    'shopName': '',
                    'categoryCode': '',
                    'categoryName': '',
                    'addedAt': '',
                },
                {
                    'label': 'Machine B',
                    'qrCode': 'QR-002',
                    'goodsId': '',
                    'shopId': '',
                    'shopName': '',
                    'categoryCode': '',
                    'categoryName': '',
                    'addedAt': '',
                },
            ],
        )

    def test_save_machines_writes_versioned_favorites_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            machines_file = Path(temp_dir) / 'machines.json'
            favorites = [
                {
                    'label': 'Machine A',
                    'qrCode': 'QR-001',
                    'goodsId': 'goods-1',
                    'shopId': 'room-1',
                    'shopName': 'Room One',
                    'categoryCode': '00',
                    'categoryName': 'Washer',
                    'addedAt': '2026-03-29T12:00:00+08:00',
                }
            ]
            with patch.object(config, 'MACHINES_FILE', machines_file):
                saved = config.save_machines(favorites)
                payload = json.loads(machines_file.read_text(encoding='utf-8'))

        self.assertEqual(saved, favorites)
        self.assertEqual(payload['version'], 2)
        self.assertEqual(payload['favorites'], favorites)


if __name__ == '__main__':
    unittest.main()

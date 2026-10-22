import types
import unittest
from unittest.mock import MagicMock, patch

from services import order_watch as ow
from services import workflow as wf


def _settings():
    return types.SimpleNamespace(token='token', pushplus_url='pushplus-token')


class NeverStartedOrderTests(unittest.TestCase):
    """待付款就取消的订单不能触发「洗衣已结束」通知。"""

    def setUp(self):
        self._row = {
            'start_notified': 0,
            'ending_notified': 0,
            'finished_notified': 0,
            'finish_time': None,
            'missing_since': None,
        }

    def test_pending_order_past_finish_time_sends_no_end_notification(self):
        client = MagicMock()
        client.get_underway_orders.return_value = {
            'ok': True,
            'data': [{'orderNo': 'P1', 'stateDesc': '待付款', 'projectName': '机器A'}],
        }
        client.order_detail.return_value = {
            'ok': True,
            'data': {'fulfillInfo': {'fulfillingItem': {'finishTime': '2026-10-22 15:00:00'}}},
        }
        with patch.object(ow, 'HaierClient', return_value=client), \
            patch.object(ow.settings_store, 'get_effective_settings', side_effect=_settings), \
            patch.object(ow.order_watch_service, '_upsert', return_value=dict(self._row)), \
            patch.object(ow.order_watch_service, '_mark') as mark, \
            patch.object(ow.order_watch_service, '_notify') as notify:
            ow.order_watch_service.tick()

        # 从未开始的订单连结束时间都不会去查，直接跳过，不产生任何通知
        notify.assert_not_called()
        mark.assert_not_called()

    def test_started_order_past_finish_time_notifies_end(self):
        client = MagicMock()
        client.get_underway_orders.return_value = {
            'ok': True,
            'data': [{'orderNo': 'R1', 'stateDesc': '运行中', 'projectName': '机器A'}],
        }
        client.order_detail.return_value = {
            'ok': True,
            'data': {'fulfillInfo': {'fulfillingItem': {'finishTime': '2026-10-22 15:00:00'}}},
        }
        row = dict(self._row)
        row['start_notified'] = 1
        with patch.object(ow, 'HaierClient', return_value=client), \
            patch.object(ow.settings_store, 'get_effective_settings', side_effect=_settings), \
            patch.object(ow.order_watch_service, '_upsert', return_value=row), \
            patch.object(ow.order_watch_service, '_mark') as mark, \
            patch.object(ow.order_watch_service, '_notify', return_value={'sent': True}) as notify:
            report = ow.order_watch_service.tick()

        notify.assert_called_once()
        self.assertIn('结束', notify.call_args[0][0])
        mark.assert_any_call('R1', 'finished_notified')
        self.assertEqual(report['finished'], 1)

    def test_missing_unstarted_order_is_archived_silently(self):
        row = {
            'order_no': 'P1',
            'machine_name': '机器A',
            'finish_time': '2026-10-22T15:00:00+08:00',
            'start_notified': 0,
            'finished_notified': 0,
            'missing_since': '2026-10-22T14:00:00+08:00',
        }
        with patch.object(ow.order_watch_service, '_mark') as mark, \
            patch.object(ow.order_watch_service, '_notify') as notify, \
            patch.object(ow, 'database') as db:
            db.fetch_all.return_value = [row]
            ow.order_watch_service._handle_missing(set(), ow.now_local(), {'finished': 0, 'notifyFailed': 0})

        notify.assert_not_called()
        mark.assert_any_call('P1', 'finished_notified')


class FastPathCacheTests(unittest.TestCase):
    def setUp(self):
        wf._SCAN_SNAPSHOT_CACHE.clear()
        wf._GOODS_DETAIL_CACHE.clear()

    def tearDown(self):
        wf._SCAN_SNAPSHOT_CACHE.clear()
        wf._GOODS_DETAIL_CACHE.clear()

    def _state(self, qr='QR-1'):
        return wf.ProcessState(
            process_id='p1',
            qr_code=qr,
            mode_id=100,
            flow_type=wf.MANUAL_SCAN_FLOW_TYPE,
        )

    def test_step_scan_reuses_cleanup_snapshot(self):
        wf._remember_scan_snapshot('QR-1', {'goodsId': 'g1', 'activityHashKey': 'h1'})
        client = MagicMock()
        state = self._state()

        result = wf.WorkflowManager()._step_scan(state, client)

        self.assertEqual(result['status'], 'success')
        self.assertEqual(state.context['goods_id'], 'g1')
        self.assertEqual(state.context['hash_key'], 'h1')
        self.assertEqual(state.current_step, 2)
        client.scan_goods.assert_not_called()
        self.assertEqual(result['debug']['source'], 'cleanup_scan_reused')

    def test_step_scan_falls_back_when_snapshot_expired(self):
        wf._remember_scan_snapshot('QR-1', {'goodsId': 'g1'})
        wf._SCAN_SNAPSHOT_CACHE['QR-1'] = (0.0, {'goodsId': 'g1'})  # 已过期
        client = MagicMock()
        client.scan_goods.return_value = {'ok': True, 'data': {'goodsId': 'g2', 'activityHashKey': 'h2'}, 'raw': {}}

        result = wf.WorkflowManager()._step_scan(self._state(), client)

        self.assertEqual(result['status'], 'success')
        client.scan_goods.assert_called_once_with('QR-1')

    def test_goods_detail_is_cached(self):
        client = MagicMock()
        client.goods_details.return_value = {'ok': True, 'data': {'id': 'g1', 'items': [{'id': 1}]}, 'raw': {}}

        first = wf.WorkflowManager()._load_goods_detail(client, 'g1')
        second = wf.WorkflowManager()._load_goods_detail(client, 'g1')

        self.assertTrue(first['ok'])
        self.assertTrue(second['ok'])
        self.assertEqual(client.goods_details.call_count, 1)
        self.assertEqual(second['raw']['source'], 'goods_detail_cache')

    def test_goods_detail_not_cached_on_failure(self):
        client = MagicMock()
        client.goods_details.return_value = {'ok': False, 'msg': 'boom', 'raw': {}}

        wf.WorkflowManager()._load_goods_detail(client, 'g9')
        wf.WorkflowManager()._load_goods_detail(client, 'g9')

        self.assertEqual(client.goods_details.call_count, 2)


if __name__ == '__main__':
    unittest.main()

class RunningStateKeywordTests(unittest.TestCase):
    def test_washing_machine_desc_is_running(self):
        # 海尔洗衣机返回的是「洗衣中」，早期只匹配「洗涤中」导致从不推「洗衣已开始」
        from services.order_watch import is_running_state
        self.assertTrue(is_running_state('洗衣中'))
        self.assertTrue(is_running_state('烘干中'))
        self.assertTrue(is_running_state('运行中'))
        self.assertFalse(is_running_state('待付款'))
        self.assertFalse(is_running_state('已完成'))


if __name__ == "__main__":
    unittest.main()

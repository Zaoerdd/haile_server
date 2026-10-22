import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from services import reservation_service as rs
from services.db import Database


def insert_task(db: Database, task_id: int, status: str) -> None:
    db.execute(
        '''
        INSERT INTO reservation_tasks(
            id, title, machine_source, machine_id, machine_name, mode_id, mode_name,
            schedule_type, target_time, lead_minutes, status, created_at, updated_at
        ) VALUES (?, ?, 'scan', 'qr-1', '机器1', 1, '标准', 'once', ?, 60, ?, ?, ?)
        ''',
        (task_id, f'任务{task_id}', '2026-10-22T15:00:00+08:00', status,
         '2026-10-22T10:00:00+08:00', '2026-10-22T10:00:00+08:00'),
    )


def read_task(db: Database, task_id: int):
    return db.fetch_one('SELECT status, last_error FROM reservation_tasks WHERE id = ?', (task_id,))


class TaskStatusGuardTests(unittest.TestCase):
    """调度器写库必须带状态条件，避免把用户刚暂停/删除的任务改回去。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / 'test.db')
        self.db.init()

    def tearDown(self):
        self.tmp.cleanup()

    def test_update_applies_when_status_is_live(self):
        insert_task(self.db, 1, 'holding')
        with patch.object(rs, 'database', self.db):
            applied = rs.reservation_service._update_task(
                1, expect_statuses=rs.LIVE_TASK_STATUSES, last_error='更新成功'
            )
        self.assertTrue(applied)
        row = read_task(self.db, 1)
        self.assertEqual(row['last_error'], '更新成功')
        self.assertEqual(row['status'], 'holding')

    def test_update_skips_paused_task(self):
        insert_task(self.db, 2, 'paused')
        with patch.object(rs, 'database', self.db):
            applied = rs.reservation_service._update_task(
                2, expect_statuses=rs.LIVE_TASK_STATUSES, status='holding', last_error='不该写入'
            )
        self.assertFalse(applied)
        row = read_task(self.db, 2)
        self.assertEqual(row['status'], 'paused', '暂停状态不能被调度器改回 holding')
        self.assertIsNone(row['last_error'])

    def test_update_skips_deleted_task(self):
        insert_task(self.db, 3, 'deleted')
        with patch.object(rs, 'database', self.db):
            applied = rs.reservation_service._update_task(
                3, expect_statuses=rs.LIVE_TASK_STATUSES, active_order_no='order-x'
            )
        self.assertFalse(applied)
        self.assertEqual(read_task(self.db, 3)['status'], 'deleted')

    def test_update_without_expectation_still_writes(self):
        insert_task(self.db, 4, 'paused')
        with patch.object(rs, 'database', self.db):
            applied = rs.reservation_service._update_task(4, last_checked_at='2026-10-22T15:10:00+08:00')
        self.assertTrue(applied)
        self.assertEqual(read_task(self.db, 4)['status'], 'paused')

    def test_execute_affected_returns_rowcount(self):
        insert_task(self.db, 5, 'scheduled')
        affected = self.db.execute_affected(
            "UPDATE reservation_tasks SET last_error = ? WHERE status = ?", ('x', 'scheduled')
        )
        self.assertEqual(affected, 1)
        affected_none = self.db.execute_affected(
            "UPDATE reservation_tasks SET last_error = ? WHERE status = ?", ('x', 'nope')
        )
        self.assertEqual(affected_none, 0)


class ProcessDueTasksSkipsStaleRowTests(unittest.TestCase):
    """取到行之后用户暂停了，本轮不能继续处理。"""

    def test_loop_skips_task_paused_after_fetch(self):
        row = {
            'id': 7,
            'title': '任务7',
            'machine_source': 'scan',
            'machine_id': 'qr-1',
            'machine_name': '机器1',
            'room_id': None,
            'room_name': None,
            'qr_code': 'qr-1',
            'mode_id': 1,
            'mode_name': '标准',
            'schedule_type': 'once',
            'target_time': '2026-10-22T15:00:00+08:00',
            'weekday': None,
            'time_of_day': None,
            'timezone_name': 'Asia/Shanghai',
            'lead_minutes': 60,
            'status': 'scheduled',
            'active_order_no': None,
            'start_at': None,
            'hold_until': None,
            'last_checked_at': None,
            'last_error': None,
            'current_order_snapshot': None,
            'last_run_at': None,
            'created_at': '2026-10-22T10:00:00+08:00',
            'updated_at': '2026-10-22T10:00:00+08:00',
        }
        with patch.object(rs, 'database') as db, \
            patch.object(rs, 'settings_store') as store, \
            patch.object(rs.reservation_service, '_create_pending_order') as create_mock:
            db.fetch_all.return_value = [row]
            db.fetch_one.return_value = {'status': 'paused'}
            store.get_effective_settings.return_value.token = 'token'

            result = rs.reservation_service.process_due_tasks()

        create_mock.assert_not_called()
        self.assertEqual(result['created'], 0)
        self.assertEqual(result['processed'], 0)


if __name__ == '__main__':
    unittest.main()


class CreateOrderKeepsOrderNoTests(unittest.TestCase):
    """建单成功但后续读取失败时，必须把 order_no 落库，避免下一轮重复建单。"""

    def _build_task(self):
        return rs.ReservationTask(
            id=11,
            title='任务11',
            machine_source='scan',
            machine_id='qr-1',
            machine_name='机器1',
            room_id=None,
            room_name=None,
            qr_code='qr-1',
            mode_id=100,
            mode_name='标准',
            schedule_type='once',
            target_time=rs.parse_iso('2026-10-22T15:00:00+08:00'),
            weekday=None,
            time_of_day=None,
            timezone_name='Asia/Shanghai',
            lead_minutes=60,
            status='scheduled',
            active_order_no=None,
            start_at=None,
            hold_until=None,
            last_checked_at=None,
            last_error=None,
            current_order_snapshot=None,
            last_run_at=None,
            created_at=rs.parse_iso('2026-10-22T10:00:00+08:00'),
            updated_at=rs.parse_iso('2026-10-22T10:00:00+08:00'),
        )

    def test_order_no_is_persisted_when_detail_read_fails(self):
        client = MagicMock()
        client.scan_goods.return_value = {'ok': True, 'data': {'goodsId': 'g1', 'activityHashKey': 'h1'}}
        client.goods_details.return_value = {
            'ok': True,
            'data': {'id': 'g1', 'categoryCode': '00', 'items': [{'id': 100, 'price': '0.00'}]},
        }
        client.verify_goods_detail.return_value = {'ok': True, 'raw': {}}
        client.create_scan_order.return_value = {'ok': True, 'data': {'orderNo': 'NEW-1'}, 'raw': {}}
        client.order_detail.return_value = {'ok': False, 'msg': 'detail boom', 'raw': {}}

        with patch.object(rs, 'HaierClient', return_value=client), \
            patch.object(rs.reservation_service, '_find_existing_pending_order', return_value=(None, None, None)), \
            patch.object(rs.reservation_service, '_update_task', return_value=True) as update_mock:
            ok, result, detail, source = rs.reservation_service._create_pending_order(self._build_task(), 'token')

        self.assertFalse(ok, '读取详情失败应返回失败')
        self.assertEqual(source, 'failed')
        self.assertEqual(result, 'detail boom')

        order_no_calls = [call for call in update_mock.call_args_list
                          if call.kwargs.get('active_order_no') == 'NEW-1']
        self.assertTrue(order_no_calls, '建单成功后必须把 order_no 写入任务，否则下一轮会重复建单')
        self.assertEqual(order_no_calls[0].kwargs.get('expect_statuses'), rs.LIVE_TASK_STATUSES,
                         '落库必须带状态守卫，避免把已暂停任务重新激活')

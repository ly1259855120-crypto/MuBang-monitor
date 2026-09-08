import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from monitor.sources import Announcement, SSE, TZ, in_window
from monitor.store import Store
from monitor.__main__ import run

NOW = datetime(2026, 9, 7, 18, tzinfo=TZ)

def notice(source='SSE', url='https://www.sse.com.cn/a.pdf', title='关于重大事项的公告'):
    return Announcement(source, '603398', title, '2026-09-07', '2026-001', '123', url)

class Source:
    def __init__(self, name='SSE', items=None, fail=False):
        self.name, self.items, self.fail = name, items or [], fail
    def fetch(self, code, start, end):
        if self.fail:
            raise ValueError('bad schema')
        return self.items

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'test.sqlite3')
        self.mails = []
    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()
    def send(self, *args):
        self.mails.append(args)
    def test_repeated_fetch_and_restart_no_duplicate(self):
        source = Source(items=[notice()])
        run(self.store, [source], self.send, NOW)
        self.store.db.close()
        self.store = Store(Path(self.tmp.name) / 'test.sqlite3')
        run(self.store, [source], self.send, NOW + timedelta(minutes=10))
        self.assertEqual(len(self.mails), 1)
    def test_backup_merge_and_changed_pdf_preserved(self):
        run(self.store, [Source(items=[notice()]), Source('CNINFO', [notice('CNINFO', 'https://static.cninfo.com.cn/b.pdf')])], self.send, NOW)
        self.assertEqual(len(self.mails), 1)
        run(self.store, [Source(items=[notice(url='https://www.sse.com.cn/corrected.pdf')])], self.send, NOW)
        self.assertEqual(len(self.mails), 2)
    def test_smtp_failure_retries_without_losing(self):
        def fail(*args):
            raise OSError('SMTP down')
        self.assertEqual(run(self.store, [Source(items=[notice()])], fail, NOW), 1)
        self.assertEqual(len(self.store.pending()), 1)
        run(self.store, [Source()], self.send, NOW)
        self.assertEqual(len(self.mails), 1)
        self.assertEqual(self.store.pending(), [])
    def test_main_failure_backup_still_notifies(self):
        code = run(self.store, [Source(fail=True), Source('CNINFO', [notice('CNINFO', 'https://static.cninfo.com.cn/b.pdf')])], self.send, NOW)
        self.assertEqual(code, 1)
        self.assertTrue(any('新公告' in x[1] for x in self.mails))
        self.assertTrue(any('监控异常' in x[1] for x in self.mails))
        self.assertIsNone(self.store.get('last_success'))
    def test_heartbeat_once_and_complete_next_day(self):
        late = NOW.replace(hour=23)
        run(self.store, [Source()], self.send, late)
        run(self.store, [Source()], self.send, late + timedelta(minutes=10))
        self.assertEqual(sum('每日心跳' in x[1] for x in self.mails), 1)
        run(self.store, [Source()], self.send, late + timedelta(hours=1))
        self.assertEqual(sum('每日日报' in x[1] for x in self.mails), 1)
        report = next(x[2] for x in self.mails if '每日日报' in x[1])
        self.assertIn('检查次数：2', report)
    def test_empty_source_is_not_failure(self):
        self.assertEqual(run(self.store, [Source()], self.send, NOW), 0)
        self.assertEqual(self.mails, [])
    def test_schema_and_security_validation(self):
        with self.assertRaises(KeyError):
            SSE.parse({'TITLE': 'test'}, '603398')
        with self.assertRaises(ValueError):
            SSE.parse({'TITLE': 'test', 'SSEDATE': '2026-09-07', 'URL': 'https://evil.test/a.pdf'}, '603398')
    def test_date_only_boundary_included(self):
        self.assertTrue(in_window('2026-09-05', NOW - timedelta(hours=48), NOW))
        self.assertFalse(in_window('2026-09-04', NOW - timedelta(hours=48), NOW))
    def test_ai_cannot_analyze_unsent_raw_notice(self):
        from monitor.ai import process
        def fail(*args):
            raise OSError('SMTP down')
        run(self.store, [Source(items=[notice()])], fail, datetime.now(TZ))
        with patch('monitor.ai.analyze') as analyzer:
            process(self.store, analyzer=analyzer, sender=self.send)
            analyzer.assert_not_called()
    def test_ai_failure_leaves_raw_sent(self):
        from monitor.ai import process
        run(self.store, [Source(items=[notice()])], self.send, datetime.now(TZ))
        def fail(item):
            raise TimeoutError('model timeout')
        process(self.store, analyzer=fail, sender=self.send)
        self.assertEqual(sum('新公告' in x[1] for x in self.mails), 1)
        self.assertTrue(any('AI分析失败' in x[1] for x in self.mails))
        self.assertIsNotNone(self.store.db.execute("SELECT sent FROM outbox WHERE key='notice:1'").fetchone()[0])
    def test_ai_summary_reused_after_mail_failure(self):
        from monitor.ai import process
        run(self.store, [Source(items=[notice()])], self.send, datetime.now(TZ))
        def fail(*args):
            raise OSError('SMTP down')
        process(self.store, analyzer=lambda item: 'summary', sender=fail)
        with patch('monitor.ai.analyze') as analyzer:
            process(self.store, analyzer=analyzer, sender=self.send)
            analyzer.assert_not_called()
        self.assertTrue(any('AI辅助摘要' in x[1] for x in self.mails))
    def test_source_missing_list_is_not_empty_success(self):
        from unittest.mock import Mock
        http = Mock()
        http.get.return_value.json.return_value = {'pageHelp': {'total': 0}}
        with self.assertRaises(ValueError):
            SSE(http).fetch('603398', NOW-timedelta(hours=48), NOW)
    def test_source_pagination_collects_all(self):
        from unittest.mock import Mock
        http = Mock()
        def row(i):
            return {'TITLE': str(i), 'SSEDATE': '2026-09-07', 'URL': f'/a/{i}.pdf'}
        first, second = Mock(), Mock()
        first.json.return_value = {'pageHelp': {'total': 101, 'data': [row(i) for i in range(100)]}}
        second.json.return_value = {'pageHelp': {'total': 101, 'data': [row(100)]}}
        http.get.side_effect = [first, second]
        items = SSE(http).fetch('603398', NOW-timedelta(hours=48), NOW)
        self.assertEqual(len(items), 101)
        self.assertEqual(http.get.call_args_list[1].kwargs['params']['pageHelp.pageNo'], 2)
    def test_same_title_different_number_not_merged(self):
        a, b = notice(), notice('CNINFO', 'https://static.cninfo.com.cn/b.pdf')
        b.number = '2026-002'
        run(self.store, [Source(items=[a]), Source('CNINFO', [b])], self.send, NOW)
        self.assertEqual(sum('新公告' in x[1] for x in self.mails), 2)

if __name__ == '__main__':
    unittest.main()

import argparse
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
from dotenv import load_dotenv
from . import mail
from .sources import SSE, CNInfo, TZ, title_key
from .store import Store

def raw_body(a, now):
    return (f'证券代码：{a.code}\n公司：*ST沐邦（简称仅作标签，以官方披露为准）\n'
            f'公告名称：{a.title}\n披露日期：{a.date}（网站可能不提供精确时分）\n'
            f'公告编号：{a.number or "源未提供；未猜测"}\n来源记录ID：{a.source_id or "未提供"}\n'
            f'来源：{a.source}\n公告PDF/URL：{a.url}\n检测时间：{now}\n'
            '本邮件为原始公告提醒，不依赖AI。备用源公告尚需上交所核对。')

def flush(store, sender, now):
    failures = 0
    for key, subject, body in store.pending():
        try:
            sender(key, subject, body)
            store.sent(key, now)
        except Exception as exc:
            # Do not log exception contents: provider responses can contain secrets.
            print(f'Mail delivery failed ({type(exc).__name__}); retained for retry.', file=sys.stderr)
            failures += 1
    return failures

def run(store, sources, sender, now=None, force_heartbeat=False):
    now = now or datetime.now(TZ)
    stamp, day = now.isoformat(), now.date().isoformat()
    start = now - timedelta(hours=48)
    previous = store.get('last_attempt')
    issues, status, results = [], {}, {}
    if previous and now - datetime.fromisoformat(previous) > timedelta(minutes=30):
        issues.append(f'距上次运行已超过30分钟，上次：{previous}；本次补查48小时，超出窗口可能漏报。')
    store.set('last_attempt', stamp)
    store.count(day, 'checks')
    for source in sources:
        try:
            results[source.name] = source.fetch('603398', start, now)
            status[source.name] = f'正常，窗口内{len(results[source.name])}条'
        except Exception as exc:
            status[source.name] = f'异常：{type(exc).__name__}'
            issues.append(f'{source.name}抓取异常：{type(exc).__name__}。请人工核对公告页并检查接口/网络。')
    status.setdefault('CNINFO', '关闭')
    if 'SSE' in results:
        store.set('last_success', stamp)
        store.count(day, 'success')
    store.set('source_status', status)
    main_keys = {(title_key(a.title), a.date) for a in results.get('SSE', [])}
    for source_name, announcements in results.items():
        for a in announcements:
            mismatch = source_name != 'SSE' and (title_key(a.title), a.date) not in main_keys
            nid, fresh = store.remember(a, stamp)
            if fresh:
                store.enqueue(f'notice:{nid}', f'【*ST沐邦新公告】{a.title}', raw_body(a, stamp))
                store.db.execute('UPDATE days SET found=found+1 WHERE day=?', (day,))
            if mismatch:
                store.enqueue(f'cross:{nid}', '【监控异常】备用源发现公告，主源本轮未确认', raw_body(a, stamp))
            store.db.commit()
    if issues:
        # One alert per issue class/day; next day repeats if still unhealthy.
        import hashlib
        issue_kind = '|'.join(sorted(k for k, v in status.items() if v.startswith('异常')))
        if previous and now - datetime.fromisoformat(previous) > timedelta(minutes=30):
            issue_kind += '|gap'
        digest = hashlib.sha256(issue_kind.encode()).hexdigest()[:12]
        store.enqueue(f'alert:{day}:{digest}', '【监控异常】603398 公告监控', '\n'.join(issues) + f'\n检测时间：{stamp}\n源状态：{status}')
    old_issue = store.get('unhealthy', False)
    if old_issue and not issues:
        store.enqueue(f'recovery:{stamp}', '【监控恢复】603398 公告监控', f'检测时间：{stamp}\n源状态：{status}')
    store.set('unhealthy', bool(issues))
    # After midnight, send complete statistics for every unsummarized active day.
    # Also send a same-day heartbeat after the configured hour (first run afterwards).
    for report_day, checks, found, success in store.db.execute('SELECT day,checks,found,success FROM days ORDER BY day').fetchall():
        final = report_day < day
        due = final or (report_day == day and (force_heartbeat or now.hour >= int(os.getenv('HEARTBEAT_HOUR', '23'))))
        if due:
            kind = '日报' if final else '心跳'
            store.enqueue(f'heartbeat:{report_day}:{kind}', f'【每日{kind}】*ST沐邦 {report_day}',
                          f'统计日期（北京时间）：{report_day}\n检查次数：{checks}\n发现公告数（去重）：{found}\n'
                          f'主源成功次数：{success}\n最近一次主源成功运行：{store.get("last_success", "尚无")}\n'
                          f'主源/备用源当前状态：{status}\n生成时间：{stamp}\n'
                          '心跳为生成时统计；次日补发完整日报。若未按时收到，请查看Actions运行记录。')
    store.db.commit()
    failures = flush(store, sender, stamp)
    print(f'Checks completed. Sources={status}; mail_failures={failures}')
    return 1 if failures or issues or any(k.startswith('cross:') for k, _, _ in store.pending()) else 0

def main():
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', action='store_true', help='Fetch only; no mail or state changes')
    parser.add_argument('--heartbeat', action='store_true', help='Request today heartbeat now')
    args = parser.parse_args()
    sources = [SSE()]
    if os.getenv('ENABLE_BACKUP', 'true').lower() == 'true':
        sources.append(CNInfo())
    if args.probe:
        now, failed = datetime.now(TZ), False
        for source in sources:
            try:
                items = source.fetch('603398', now - timedelta(hours=48), now)
                print(source.name, len(items), 'announcements')
                for item in items:
                    print(item.date, item.title, item.url)
            except Exception as exc:
                print(source.name, type(exc).__name__, str(exc)[:300])
                failed = True
        return int(failed)
    path = Path(os.getenv('STATE_DB', 'state/monitor.sqlite3'))
    path.parent.mkdir(parents=True, exist_ok=True)
    return run(Store(path), sources, mail.send, force_heartbeat=args.heartbeat)

if __name__ == '__main__':
    sys.exit(main())

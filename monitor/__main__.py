import argparse
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
from dotenv import load_dotenv
from . import mail
from .sources import SSE, CNInfo, TZ
from .store import Store

def raw_body(a, now):
    return (f'证券代码：{a.code}\n公司：*ST沐邦（简称仅作标签，以官方披露为准）\n'
            f'公告名称：{a.title}\n披露日期：{a.date}（网站可能不提供精确时分）\n'
            f'公告编号：{a.number or "源未提供；未猜测"}\n来源记录ID：{a.source_id or "未提供"}\n'
            f'来源：{a.source}\n公告PDF/URL：{a.url}\n检测时间：{now}\n'
            '本邮件为原始公告提醒，不依赖AI。备用源公告尚需上交所核对。')

def flush(store, sender, now):
    # User preference: no operational exception/recovery emails, including
    # legacy queued ones and optional AI error notifications.
    store.db.execute("""DELETE FROM outbox WHERE sent IS NULL AND
        (key LIKE 'alert:%' OR key LIKE 'recovery:%' OR key LIKE 'cross:%'
         OR key LIKE 'ai-failure:%')""")
    store.db.commit()
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
            response = getattr(exc, 'response', None)
            http_code = getattr(response, 'status_code', None)
            detail = f' HTTP {http_code}' if http_code is not None else ''
            status[source.name] = f'异常：{type(exc).__name__}{detail}'
            issues.append(f'{source.name}抓取异常：{type(exc).__name__}。请人工核对公告页并检查接口/网络。')
    status.setdefault('CNINFO', '关闭')
    if 'SSE' in results:
        store.set('last_success', stamp)
        store.count(day, 'success')
    store.set('source_status', status)
    for source_name, announcements in results.items():
        for a in announcements:
            nid, fresh = store.remember(a, stamp)
            if fresh:
                store.enqueue(f'notice:{nid}', f'【*ST沐邦新公告】{a.title}', raw_body(a, stamp))
                store.db.execute('UPDATE days SET found=found+1 WHERE day=?', (day,))
            store.db.commit()
    # Faults remain in state/logs and daily status, never in separate emails.
    store.set('last_issues', issues)
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
    # Operational warnings stay in logs. Keep the workflow red
    # for primary-source or delivery failure, not optional-source degradation.
    return 1 if failures or 'SSE' not in results else 0

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

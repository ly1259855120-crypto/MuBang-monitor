import json
import sqlite3
from dataclasses import asdict
from .sources import title_key

class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notices (
          id INTEGER PRIMARY KEY, code TEXT, title TEXT, date TEXT, number TEXT, discovered TEXT);
        CREATE TABLE IF NOT EXISTS versions (
          notice INTEGER, source TEXT, source_id TEXT, url TEXT, data TEXT,
          UNIQUE(source, url));
        CREATE TABLE IF NOT EXISTS outbox (
          key TEXT PRIMARY KEY, subject TEXT, body TEXT, sent TEXT);
        CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, checks INTEGER DEFAULT 0,
          found INTEGER DEFAULT 0, success INTEGER DEFAULT 0);
        ''')

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', (key, json.dumps(value)))
        self.db.commit()

    def count(self, day, field):
        assert field in ('checks', 'found', 'success')
        self.db.execute('INSERT OR IGNORE INTO days(day) VALUES (?)', (day,))
        self.db.execute(f'UPDATE days SET {field}={field}+1 WHERE day=?', (day,))
        self.db.commit()

    def remember(self, a, now):
        # URL is strongest. Same-source changed URL is conservatively a new version,
        # even if its title/date/id are identical (avoid suppressing corrections).
        old = self.db.execute('SELECT notice FROM versions WHERE source=? AND url=?', (a.source, a.url)).fetchone()
        if old:
            return old[0], False
        old = self.db.execute('''SELECT n.id FROM notices n JOIN versions v ON v.notice=n.id
          WHERE n.code=? AND n.title=? AND n.date=? AND (n.number=? OR n.number='' OR ?='')
          AND v.source<>? AND NOT EXISTS (SELECT 1 FROM versions v2 WHERE v2.notice=n.id AND v2.source=?)''',
          (a.code, title_key(a.title), a.date, a.number, a.number, a.source, a.source)).fetchone()
        fresh = old is None
        if fresh:
            cur = self.db.execute('INSERT INTO notices(code,title,date,number,discovered) VALUES (?,?,?,?,?)',
                                 (a.code, title_key(a.title), a.date, a.number, now))
            nid = cur.lastrowid
        else:
            nid = old[0]
        self.db.execute('INSERT INTO versions VALUES (?,?,?,?,?)', (nid, a.source, a.source_id, a.url, json.dumps(asdict(a), ensure_ascii=False)))
        # Caller commits this together with its notification to avoid a silent gap.
        return nid, fresh

    def enqueue(self, key, subject, body):
        self.db.execute('INSERT OR IGNORE INTO outbox(key,subject,body) VALUES (?,?,?)', (key, subject, body))

    def pending(self):
        return self.db.execute('SELECT key,subject,body FROM outbox WHERE sent IS NULL ORDER BY rowid').fetchall()

    def sent(self, key, now):
        self.db.execute('UPDATE outbox SET sent=? WHERE key=?', (now, key))
        self.db.commit()

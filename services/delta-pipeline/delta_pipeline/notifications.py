"""Durable incident inbox and bounded, opt-in delivery. No default external sender."""

import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

from delta_ai.diagnostic_contract import safe_text


class Inbox:
    def __init__(self, directory):
        self.directory = Path(directory)
        if self.directory.is_symlink():
            raise ValueError('Unsafe inbox directory')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'incidents.sqlite'
        if self.path.is_symlink():
            raise ValueError('Unsafe inbox file')
        with self.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS incidents (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, payload TEXT NOT NULL, read_at REAL, delivery TEXT NOT NULL DEFAULT "pending", attempts INTEGER NOT NULL DEFAULT 0)')
        os.chmod(self.path, 0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def emit(self, *, run_id, case_id, kind, summary, actions=()):
        if not all(isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', v)
                   for v in (run_id, case_id, kind)):
            raise ValueError('Invalid incident identity')
        event_id = hashlib.sha256(f'{run_id}/{case_id}/{kind}'.encode()).hexdigest()
        payload = {'id': event_id, 'run_id': run_id, 'case_id': case_id, 'kind': kind,
            'summary': safe_text(summary)[:1000], 'actions': list(actions), 'created_at': time.time(),
            'report_path': f'/api/pipeline/runs/{run_id}/reports/{case_id}/md'}
        with self.connect() as conn:
            conn.execute('INSERT OR IGNORE INTO incidents(id,run_id,payload) VALUES(?,?,?)',
                         (event_id, run_id, json.dumps(payload, ensure_ascii=False)))
        return event_id

    def list(self, run_id, limit=100):
        with self.connect() as conn:
            rows = conn.execute('SELECT payload,read_at,delivery,attempts FROM incidents WHERE run_id=? ORDER BY rowid DESC LIMIT ?',
                                (run_id, min(max(int(limit), 1), 100))).fetchall()
        return [{**json.loads(p), 'read_at': r, 'delivery': d, 'attempts': a} for p, r, d, a in rows]

    def acknowledge(self, run_id, event_id):
        with self.connect() as conn:
            return conn.execute('UPDATE incidents SET read_at=COALESCE(read_at,?) WHERE run_id=? AND id=?',
                                (time.time(), run_id, event_id)).rowcount == 1

    def deliver(self, event_id, sender):
        # At-least-once delivery: the receiver can deduplicate by the stable idempotency key.
        with self.connect() as conn:
            row = conn.execute('SELECT payload,delivery,attempts FROM incidents WHERE id=?', (event_id,)).fetchone()
        if not row or row[1] == 'sent' or row[2] >= 3:
            return False
        try:
            sender(json.loads(row[0]))
            status = 'sent'
        except Exception:  # noqa: BLE001 — no endpoint/token or upstream body in delivery status
            status = 'failed'
        with self.connect() as conn:
            conn.execute('UPDATE incidents SET delivery=?, attempts=attempts+1 WHERE id=?', (status, event_id))
        return status == 'sent'


class Webhook:
    def __init__(self, url, origin, *, allowed_hosts=('hooks.slack.com',)):
        from delta_diff.client import _NoRedirect

        parsed, public = urllib.parse.urlparse(url), urllib.parse.urlparse(origin)
        loopback = parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')
        if (not loopback and (parsed.scheme != 'https' or parsed.hostname not in allowed_hosts)
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError('Webhook destination must be operator-allowlisted HTTPS')
        if (public.scheme not in ('https', 'http') or not public.hostname or public.username
                or public.password or public.query or public.fragment or public.path not in ('', '/')
                or public.scheme == 'http' and public.hostname not in ('127.0.0.1', 'localhost', '::1')):
            raise ValueError('Invalid public origin')
        self.url, self.origin = url, origin.rstrip('/')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def __call__(self, event):
        body = json.dumps({'text': f"{event['summary']}\n실행: {event['run_id']}\n보고서: {self.origin}{event['report_path']}"}, ensure_ascii=False).encode()
        req = urllib.request.Request(self.url, body, method='POST', headers={
            'Content-Type': 'application/json', 'Idempotency-Key': event['id']})
        with self.opener.open(req, timeout=5) as response:
            if not 200 <= response.status < 300:
                raise ValueError('Delivery failed')

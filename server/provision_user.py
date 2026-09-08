import hashlib
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timezone

user_id = (sys.argv[1] if len(sys.argv) > 1 else 'Admin').strip()
if not user_id or len(user_id) > 40:
    raise SystemExit('用法：python3 provision_user.py <user-id>')
db_path = os.getenv('CLASS_TABLE_DB', '/opt/apps/class-table-api/data/class-table.sqlite3')
token = secrets.token_urlsafe(32)
stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
conn = sqlite3.connect(db_path)
conn.execute('''CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY COLLATE NOCASE, token_hash TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
conn.execute('''CREATE TABLE IF NOT EXISTS timetables (user_id TEXT PRIMARY KEY COLLATE NOCASE, revision INTEGER NOT NULL DEFAULT 0, state_json TEXT NOT NULL, updated_at TEXT NOT NULL)''')
row = conn.execute('SELECT created_at FROM users WHERE user_id=?', (user_id,)).fetchone()
created = row[0] if row else stamp
conn.execute('INSERT INTO users(user_id, token_hash, created_at, updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET token_hash=excluded.token_hash, updated_at=excluded.updated_at', (user_id, hashlib.sha256(token.encode()).hexdigest(), created, stamp))
if not row:
    conn.execute('INSERT INTO timetables(user_id, revision, state_json, updated_at) VALUES(?,0,?,?)', (user_id, '{"settings":{},"courses":[]}', stamp))
conn.commit()
conn.close()
print(f'userId={user_id}')
print(f'syncKey={token}')

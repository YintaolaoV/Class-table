import base64
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request
from flask_cors import CORS

try:
    from pywebpush import webpush, WebPushException
except ImportError:  # API 仍可在未配置推送依赖时提供同步服务
    webpush = None
    WebPushException = Exception

APP = Flask(__name__)
ORIGIN = os.getenv('CLASS_TABLE_CORS_ORIGIN', 'https://class.vincentlee.asia')
CORS(APP, resources={r'/v1/*': {'origins': [ORIGIN]}})
DB_PATH = os.getenv('CLASS_TABLE_DB', '/data/class-table.sqlite3')
BOOTSTRAP_KEY = os.getenv('CLASS_TABLE_BOOTSTRAP_KEY', '')
VAPID_PUBLIC_KEY = os.getenv('VAPID_PUBLIC_KEY', '')
VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY', '')
if os.getenv('VAPID_PRIVATE_KEY_B64'):
    try:
        VAPID_PRIVATE_KEY = base64.b64decode(os.getenv('VAPID_PRIVATE_KEY_B64', '')).decode('utf-8')
    except Exception:
        VAPID_PRIVATE_KEY = ''
VAPID_SUBJECT = os.getenv('VAPID_SUBJECT', 'mailto:admin@vincentlee.asia')
TZ = ZoneInfo('Asia/Shanghai')
MAX_STATE_BYTES = 1024 * 1024
PASSWORD_MIN_LENGTH = 4

SCHOOL_PERIODS = [
    ('08:20', '09:00'), ('09:10', '09:50'), ('10:15', '10:55'),
    ('11:05', '11:45'), ('14:00', '14:40'), ('14:50', '15:30'),
    ('15:55', '16:35'), ('16:45', '17:25'), ('19:00', '19:40'),
    ('19:50', '20:30')
]


def db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript('''
      CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY COLLATE NOCASE,
        token_hash TEXT NOT NULL,
        password_hash TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS timetables (
        user_id TEXT PRIMARY KEY COLLATE NOCASE,
        revision INTEGER NOT NULL DEFAULT 0,
        state_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
      );
      CREATE TABLE IF NOT EXISTS push_subscriptions (
        user_id TEXT NOT NULL,
        endpoint TEXT NOT NULL,
        subscription_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(user_id, endpoint),
        FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
      );
      CREATE TABLE IF NOT EXISTS sent_notifications (
        user_id TEXT NOT NULL,
        course_key TEXT NOT NULL,
        class_date TEXT NOT NULL,
        kind TEXT NOT NULL,
        sent_at TEXT NOT NULL,
        PRIMARY KEY(user_id, course_key, class_date, kind)
      );
      CREATE TABLE IF NOT EXISTS analytics_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        event_name TEXT NOT NULL,
        event_target TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL DEFAULT '',
        level TEXT NOT NULL DEFAULT 'info',
        event_type TEXT NOT NULL,
        message TEXT NOT NULL DEFAULT '',
        status_code INTEGER,
        created_at TEXT NOT NULL
      );
    ''')
    # 兼容旧版已创建的 users 表
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(users)').fetchall()}
    if 'password_hash' not in columns:
        conn.execute('ALTER TABLE users ADD COLUMN password_hash TEXT')
    return conn


def now_text():
    return datetime.now(TZ).isoformat(timespec='seconds')


def audit(user_id='', event_type='system', message='', level='info', status_code=None):
    try:
        conn = db()
        conn.execute('INSERT INTO audit_logs(user_id,level,event_type,message,status_code,created_at) VALUES(?,?,?,?,?,?)', (str(user_id)[:40], str(level)[:12], str(event_type)[:60], str(message)[:240], status_code, now_text()))
        conn.commit()
        conn.close()
    except Exception:
        APP.logger.exception('audit log failed')


def token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 210000)
    return f'pbkdf2$210000${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}'


def password_matches(password, encoded):
    try:
        scheme, rounds, salt_text, digest_text = encoded.split('$', 3)
        if scheme != 'pbkdf2':
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        digest = base64.urlsafe_b64decode(digest_text.encode())
        check = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, int(rounds))
        return secrets.compare_digest(check, digest)
    except Exception:
        return False


@APP.post('/v1/auth/login')
def login():
    body = request.get_json(silent=True) or {}
    user_id = str(body.get('userId', '')).strip()
    password = str(body.get('password', ''))
    if not user_id or len(user_id) > 40 or any(ch in user_id for ch in '/\\\n\r'):
        return jsonify(error='invalid_user_id', message='用户 ID 无效。'), 400
    if len(password) < PASSWORD_MIN_LENGTH or len(password) > 200:
        return jsonify(error='invalid_password', message='密码至少需要 4 位。'), 400
    conn = db()
    row = conn.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)).fetchone()
    stamp = now_text()
    created = False
    if not row:
        token = secrets.token_urlsafe(32)
        conn.execute('INSERT INTO users(user_id, token_hash, password_hash, created_at, updated_at) VALUES(?,?,?,?,?)', (user_id, token_hash(token), password_hash(password), stamp, stamp))
        conn.execute('INSERT INTO timetables(user_id, state_json, updated_at) VALUES(?,?,?)', (user_id, json.dumps({'settings': {}, 'courses': []}), stamp))
        created = True
        revision = 0
    else:
        stored = row['password_hash']
        # 旧版由管理员创建的账号首次登录时设置密码，之后即按密码校验。
        if stored and not password_matches(password, stored):
            conn.close()
            audit(user_id, 'login_failed', '密码校验失败', 'warning', 401)
            return jsonify(error='invalid_credentials', message='用户 ID 或密码不正确。'), 401
        token = secrets.token_urlsafe(32)
        conn.execute('UPDATE users SET token_hash=?, password_hash=?, updated_at=? WHERE user_id=?', (token_hash(token), stored or password_hash(password), stamp, row['user_id']))
        revision_row = conn.execute('SELECT revision FROM timetables WHERE user_id=?', (row['user_id'],)).fetchone()
        revision = int(revision_row['revision']) if revision_row else 0
    conn.commit()
    conn.close()
    audit(user_id, 'user_registered' if created else 'login_success', '创建账号并登录' if created else '登录成功')
    return jsonify(userId=user_id, token=token, revision=revision, created=created)


def auth_user():
    user_id = (request.headers.get('X-User-Id') or '').strip()
    auth = request.headers.get('Authorization', '')
    token = auth[7:].strip() if auth.lower().startswith('bearer ') else ''
    if not user_id or not token:
        return None
    conn = db()
    row = conn.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    if not row or not secrets.compare_digest(row['token_hash'], token_hash(token)):
        return None
    return row['user_id']


def require_user():
    user_id = auth_user()
    if not user_id:
        return None, (jsonify(error='unauthorized', message='用户 ID 或同步密钥无效。'), 401)
    return user_id, None


def require_admin():
    user_id, error = require_user()
    if error:
        return None, error
    if str(user_id).casefold() != 'admin':
        return None, (jsonify(error='forbidden', message='只有 Admin 用户可以访问管理面板。'), 403)
    return user_id, None


@APP.get('/health')
def health():
    return jsonify(ok=True, push=bool(webpush and VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY))


@APP.post('/v1/analytics/event')
def analytics_event():
    user_id, error = require_user()
    if error:
        return error
    body = request.get_json(silent=True) or {}
    event_name = str(body.get('event', '')).strip()[:60]
    event_target = str(body.get('target', '')).strip()[:120]
    if not event_name or any(ch in event_name for ch in '\n\r'):
        return jsonify(error='invalid_event'), 400
    # 兼容旧版客户端，但不再保存全局 click/page_view 噪声。
    if event_name in ('click', 'page_view'):
        return jsonify(ok=True, ignored=True)
    conn = db()
    conn.execute('INSERT INTO analytics_events(user_id,event_name,event_target,created_at) VALUES(?,?,?,?)', (user_id, event_name, event_target, now_text()))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@APP.get('/v1/admin/overview')
def admin_overview():
    _, error = require_admin()
    if error:
        return error
    filter_user = str(request.args.get('userId', '')).strip()[:40]
    conn = db()
    users = []
    user_query = 'SELECT u.user_id,u.created_at,u.updated_at,t.revision,t.updated_at AS timetable_updated,t.state_json FROM users u LEFT JOIN timetables t ON t.user_id=u.user_id'
    user_params = ()
    if filter_user:
        user_query += ' WHERE u.user_id LIKE ?'
        user_params = (f'%{filter_user}%',)
    user_query += ' ORDER BY u.updated_at DESC'
    for row in conn.execute(user_query, user_params).fetchall():
        try:
            state = json.loads(row['state_json'] or '{}')
        except Exception:
            state = {'settings': {}, 'courses': []}
        users.append({'userId': row['user_id'], 'createdAt': row['created_at'], 'updatedAt': row['updated_at'], 'revision': row['revision'] or 0, 'timetableUpdatedAt': row['timetable_updated'], 'state': state})
    log_query = 'SELECT user_id,level,event_type,message,status_code,created_at FROM audit_logs'
    log_params = ()
    if filter_user:
        log_query += ' WHERE user_id LIKE ?'
        log_params = (f'%{filter_user}%',)
    log_query += ' ORDER BY id DESC LIMIT 500'
    events = [{'userId': row['user_id'], 'level': row['level'], 'eventType': row['event_type'], 'message': row['message'], 'statusCode': row['status_code'], 'createdAt': row['created_at']} for row in conn.execute(log_query, log_params).fetchall()]
    summary_query = 'SELECT event_type,COUNT(*) AS count FROM audit_logs'
    summary_params = ()
    if filter_user:
        summary_query += ' WHERE user_id LIKE ?'
        summary_params = (f'%{filter_user}%',)
    summary_query += ' GROUP BY event_type ORDER BY count DESC'
    summary = [{'event': row['event_type'], 'count': row['count']} for row in conn.execute(summary_query, summary_params).fetchall()]
    conn.close()
    return jsonify(users=users, events=events, summary=summary, generatedAt=now_text())


@APP.get('/v1/push/public-key')
def public_key():
    return jsonify(publicKey=VAPID_PUBLIC_KEY or None)


@APP.post('/v1/users/provision')
def provision():
    if not BOOTSTRAP_KEY or not secrets.compare_digest(request.headers.get('X-Bootstrap-Key', ''), BOOTSTRAP_KEY):
        return jsonify(error='forbidden'), 403
    body = request.get_json(silent=True) or {}
    user_id = str(body.get('userId', '')).strip()
    if not user_id or len(user_id) > 40 or any(ch in user_id for ch in '/\\\n\r'):
        return jsonify(error='invalid_user_id'), 400
    token = secrets.token_urlsafe(32)
    stamp = now_text()
    conn = db()
    try:
        conn.execute('INSERT INTO users(user_id, token_hash, created_at, updated_at) VALUES(?,?,?,?)', (user_id, token_hash(token), stamp, stamp))
        conn.execute('INSERT INTO timetables(user_id, state_json, updated_at) VALUES(?,?,?)', (user_id, json.dumps({'settings': {}, 'courses': []}), stamp))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return jsonify(error='user_exists'), 409
    finally:
        conn.close()
    return jsonify(userId=user_id, syncKey=token), 201


@APP.get('/v1/timetable')
def get_timetable():
    user_id, error = require_user()
    if error:
        return error
    conn = db()
    row = conn.execute('SELECT revision, state_json, updated_at FROM timetables WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify(error='not_found'), 404
    audit(user_id, 'timetable_pull', '拉取课表')
    return jsonify(revision=row['revision'], state=json.loads(row['state_json']), updatedAt=row['updated_at'])


@APP.put('/v1/timetable')
def put_timetable():
    user_id, error = require_user()
    if error:
        return error
    raw = request.get_data(cache=True)
    if len(raw) > MAX_STATE_BYTES:
        return jsonify(error='payload_too_large'), 413
    body = request.get_json(silent=True) or {}
    state = body.get('state')
    if not isinstance(state, dict) or not isinstance(state.get('settings'), dict) or not isinstance(state.get('courses'), list):
        return jsonify(error='invalid_state'), 400
    conn = db()
    row = conn.execute('SELECT revision FROM timetables WHERE user_id = ?', (user_id,)).fetchone()
    current = int(row['revision']) if row else 0
    client_revision = int(body.get('revision', 0) or 0)
    if row and client_revision != current:
        conn.close()
        return jsonify(error='revision_conflict', revision=current), 409
    revision = current + 1
    stamp = now_text()
    conn.execute('UPDATE timetables SET revision=?, state_json=?, updated_at=? WHERE user_id=?', (revision, json.dumps(state, ensure_ascii=False, separators=(',', ':')), stamp, user_id))
    conn.commit()
    conn.close()
    audit(user_id, 'timetable_push', f'上传课表，版本 {revision}')
    return jsonify(ok=True, revision=revision, updatedAt=stamp)


@APP.post('/v1/push/subscription')
def add_subscription():
    user_id, error = require_user()
    if error:
        return error
    body = request.get_json(silent=True) or {}
    endpoint = str(body.get('endpoint', '')).strip()
    if not endpoint or not isinstance(body.get('keys'), dict):
        return jsonify(error='invalid_subscription'), 400
    payload = json.dumps({'endpoint': endpoint, 'keys': body['keys']}, ensure_ascii=False)
    conn = db()
    conn.execute('INSERT INTO push_subscriptions(user_id, endpoint, subscription_json, updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id, endpoint) DO UPDATE SET subscription_json=excluded.subscription_json, updated_at=excluded.updated_at', (user_id, endpoint, payload, now_text()))
    conn.commit()
    conn.close()
    audit(user_id, 'push_subscribed', '建立推送订阅')
    return jsonify(ok=True)


@APP.delete('/v1/push/subscription')
def delete_subscription():
    user_id, error = require_user()
    if error:
        return error
    body = request.get_json(silent=True) or {}
    conn = db()
    if body.get('endpoint'):
        conn.execute('DELETE FROM push_subscriptions WHERE user_id=? AND endpoint=?', (user_id, body['endpoint']))
    else:
        conn.execute('DELETE FROM push_subscriptions WHERE user_id=?', (user_id,))
    conn.commit()
    conn.close()
    audit(user_id, 'push_unsubscribed', '关闭推送订阅')
    return jsonify(ok=True)


@APP.post('/v1/push/test')
def test_push():
    user_id, error = require_user()
    if error:
        return error
    if not (webpush and VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY):
        return jsonify(error='push_disabled', message='服务器推送服务未配置。'), 503
    conn = db()
    rows = conn.execute('SELECT endpoint, subscription_json FROM push_subscriptions WHERE user_id=?', (user_id,)).fetchall()
    conn.close()
    sent = 0
    failed = 0
    payload = json.dumps({'title': '课表测试推送', 'body': 'test', 'url': './'}, ensure_ascii=False)
    for row in rows:
        try:
            webpush(subscription_info=json.loads(row['subscription_json']), data=payload, vapid_private_key=VAPID_PRIVATE_KEY, vapid_claims={'sub': VAPID_SUBJECT})
            sent += 1
        except WebPushException as error:
            failed += 1
            status_code = getattr(getattr(error, 'response', None), 'status_code', None)
            audit(user_id, 'push_test_failed', f'测试推送失败：{type(error).__name__}', 'error', status_code)
        except Exception as error:
            failed += 1
            audit(user_id, 'push_test_failed', f'测试推送失败：{type(error).__name__}', 'error')
    if not rows:
        audit(user_id, 'push_test_no_subscription', '没有可用的推送订阅', 'warning')
    elif sent:
        audit(user_id, 'push_test_sent', f'测试推送成功，发送设备数 {sent}')
    return jsonify(ok=True, subscriptions=len(rows), sent=sent, failed=failed)


def minutes(value):
    hour, minute = [int(part) for part in str(value).split(':')[:2]]
    return hour * 60 + minute


def course_timing(course, settings):
    start_period = int(course.get('start', 1))
    duration = max(1, int(course.get('duration', 1)))
    location = str(course.get('location', ''))
    central = any(name in location for name in ('明志楼', '明德楼', '至善楼'))
    if settings.get('usePresetTimes', True) and 1 <= start_period <= len(SCHOOL_PERIODS):
        end_period = min(start_period + duration - 1, len(SCHOOL_PERIODS))
        start, end = SCHOOL_PERIODS[start_period - 1], SCHOOL_PERIODS[end_period - 1]
        if not central and start_period in (3, 4):
            start = ('10:25', '11:05') if start_period == 3 else ('11:15', '11:55')
        if not central and end_period in (3, 4):
            end = ('10:25', '11:05') if end_period == 3 else ('11:15', '11:55')
        return minutes(start[0]), minutes(end[1])
    first = minutes(settings.get('firstTime', '08:20'))
    length = int(settings.get('periodMinutes', 40))
    current = first
    for period in range(1, start_period):
        current += length
        if period == 4:
            current = max(current, minutes(settings.get('lunchEnd', '14:00')))
        elif period == 8:
            current = max(current, minutes(settings.get('dinnerEnd', '19:00')))
        else:
            current += int(settings.get('breakAfter2' if period == 2 else 'breakAfter6' if period == 6 else 'breakMinutes', 10))
    return current, current + duration * length


def active_on(course, date, settings):
    try:
        start = datetime.strptime(str(settings['termStart']), '%Y-%m-%d').date()
        diff = (date - start).days
        if diff < 0:
            return False
        week = diff // 7 + 1
        week_type = 'odd' if week % 2 else 'even'
        return int(course.get('day')) == (date.weekday() + 1) and int(course.get('weekStart', 1)) <= week <= int(course.get('weekEnd', 16)) and course.get('weekType', 'all') in ('all', week_type)
    except (KeyError, TypeError, ValueError):
        return False


def send_due_notifications():
    if not (webpush and VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY):
        return
    now = datetime.now(TZ).replace(second=0, microsecond=0)
    conn = db()
    rows = conn.execute('SELECT u.user_id, t.state_json FROM users u JOIN timetables t ON t.user_id=u.user_id').fetchall()
    for row in rows:
        try:
            state = json.loads(row['state_json'])
            settings, courses = state.get('settings', {}), state.get('courses', [])
            subscriptions = conn.execute('SELECT endpoint, subscription_json FROM push_subscriptions WHERE user_id=?', (row['user_id'],)).fetchall()
            for course in courses:
                if not active_on(course, now.date(), settings):
                    continue
                start, _ = course_timing(course, settings)
                departure = now.replace(hour=0, minute=0) + timedelta(minutes=start - 20)
                if departure != now:
                    continue
                key = (row['user_id'], str(course.get('id')), now.date().isoformat(), 'departure')
                try:
                    conn.execute('INSERT INTO sent_notifications VALUES(?,?,?,?,?)', (*key, now_text()))
                except sqlite3.IntegrityError:
                    continue
                payload = json.dumps({'title': '该出发了：' + str(course.get('name', '下一节课')), 'body': f"{course.get('location', '未填写地点')} · {start // 60:02d}:{start % 60:02d} 上课", 'url': './'})
                for subscription in subscriptions:
                    try:
                        webpush(subscription_info=json.loads(subscription['subscription_json']), data=payload, vapid_private_key=VAPID_PRIVATE_KEY, vapid_claims={'sub': VAPID_SUBJECT})
                        audit(row['user_id'], 'push_schedule_sent', f"课前推送成功：{course.get('name', '下一节课')}")
                    except WebPushException as error:
                        status_code = getattr(getattr(error, 'response', None), 'status_code', None)
                        audit(row['user_id'], 'push_schedule_failed', f'课前推送失败：{type(error).__name__}', 'error', status_code)
                        if status_code in (404, 410):
                            conn.execute('DELETE FROM push_subscriptions WHERE user_id=? AND endpoint=?', (row['user_id'], subscription['endpoint']))
                    except Exception as error:
                        audit(row['user_id'], 'push_schedule_failed', f'课前推送失败：{type(error).__name__}', 'error')
        except Exception:
            APP.logger.exception('notification check failed for %s', row['user_id'])
    conn.commit()
    conn.close()


def scheduler():
    while True:
        try:
            send_due_notifications()
        except Exception:
            APP.logger.exception('scheduler failed')
        time.sleep(30)


_scheduler_started = False
_scheduler_lock = threading.Lock()


@APP.before_request
def ensure_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    with _scheduler_lock:
        if not _scheduler_started:
            threading.Thread(target=scheduler, daemon=True, name='class-table-push-scheduler').start()
            _scheduler_started = True


if __name__ == '__main__':
    APP.run(host='0.0.0.0', port=int(os.getenv('PORT', '8080')))

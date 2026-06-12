# ================================================================================
# SIGNATURE DIGITAL — SGD ACCESS SERVER
# ================================================================================
# SUMMARY:
#   Full rewrite to match VEO Flow server architecture exactly.
#   Same endpoint structure, same device locking, same cookie handling,
#   same admin auth — adapted for signaturedigital.asia.
#
# DATE/TIME (SL) : 13-06-2026
# ────────────────────────────────────────────────────────────────────────────────
# [NEW] /sd-data — extension fetches license + cookies (matches /sd-flow-data)
# [NEW] /admin/set-cookies — update cookies (Netscape format)
# [NEW] /admin/get-cookies — view current cookies
# [NEW] /admin/add-license — add license with key + expires
# [NEW] /admin/revoke-license — delete license + devices
# [NEW] /admin/extend-license — extend by hours
# [NEW] /admin/reduce-license — reduce by hours
# [NEW] /admin/reset-device — clear device lock + force logout
# [NEW] /admin/set-device-limit — set max devices per key
# [NEW] /admin/check-license — full license info + active device status
# [NEW] /admin/licenses — list all licenses
# [NEW] /admin/devices — list all devices
# [NEW] /debug — debug info (admin only)
# ================================================================================

from flask import Flask, jsonify, request
from datetime import datetime, date, timedelta, timezone
import os, json, threading

app = Flask(__name__)

# ─── STORAGE ──────────────────────────────────────────────────────────────────
DATA_DIR      = '/data' if os.path.isdir('/data') else '/tmp'
LICENSE_FILE  = os.path.join(DATA_DIR, 'sgd_licenses.json')
COOKIES_FILE  = os.path.join(DATA_DIR, 'sgd_cookies.txt')

_file_lock    = threading.Lock()

# In-memory set of device IDs that were reset — forces logout on next check-in
_revoked_devices      = set()
_revoked_devices_lock = threading.Lock()

# ─── COOKIES (in-memory + persisted to sgd_cookies.txt) ──────────────────────
_cookies_netscape = ''
_cookies_lock     = threading.Lock()

def _load_cookies():
    global _cookies_netscape
    try:
        if os.path.exists(COOKIES_FILE):
            with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
                _cookies_netscape = f.read()
            app.logger.info('Cookies loaded from ' + COOKIES_FILE)
    except Exception as e:
        app.logger.error('load_cookies error: ' + str(e))

def _save_cookies(text):
    global _cookies_netscape
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = COOKIES_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, COOKIES_FILE)
        with _cookies_lock:
            _cookies_netscape = text
        app.logger.info('Cookies saved to ' + COOKIES_FILE)
        return True
    except Exception as e:
        app.logger.error('save_cookies error: ' + str(e))
        return False

# Load cookies on startup
_load_cookies()

# ─── LICENSE FILE HELPERS ─────────────────────────────────────────────────────
def _load_all():
    try:
        with _file_lock:
            with open(LICENSE_FILE, 'r') as f:
                d = json.load(f)
        return d.get('licenses', {}), d.get('devices', {}), d.get('exempt', [])
    except FileNotFoundError:
        return {}, {}, []
    except Exception as e:
        app.logger.error('sgd_licenses load error: ' + str(e))
        return {}, {}, []

def _save_all(licenses, devices, exempt):
    try:
        payload = {'licenses': licenses, 'devices': devices, 'exempt': exempt}
        tmp = LICENSE_FILE + '.tmp'
        with _file_lock:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(tmp, 'w') as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, LICENSE_FILE)
    except Exception as e:
        app.logger.error('sgd_licenses save error: ' + str(e))

# ─── ADMIN AUTH ───────────────────────────────────────────────────────────────
ADMIN_SECRET = os.environ.get('ADMIN_SECRET', 'changeme123')

def _auth(req):
    s = req.args.get('secret') or (req.get_json(silent=True) or {}).get('secret', '')
    return s == ADMIN_SECRET

# ─── CORS ─────────────────────────────────────────────────────────────────────
@app.after_request
def _cors(resp):
    resp.headers['Access-Control-Allow-Origin']  = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = '*'
    resp.headers['Cache-Control'] = 'no-store'
    return resp

# ─── PUBLIC: extension fetches license + cookies ──────────────────────────────
@app.route('/sd-data', methods=['GET', 'OPTIONS'])
def sd_data():
    if request.method == 'OPTIONS':
        return app.response_class(status=204)

    key      = request.args.get('key', '').strip().upper()
    deviceId = request.args.get('deviceId', '').strip()

    if not key:
        return jsonify({'error': 'missing key'}), 401

    licenses, devices, exempt = _load_all()

    entry = licenses.get(key)
    if not entry:
        return jsonify({'error': 'invalid key'}), 401

    # Expiry check
    try:
        raw = entry.get('expires', '')
        if not raw:
            return jsonify({'error': 'license expired'}), 403
        try:
            expiry = datetime.fromisoformat(raw)
        except Exception:
            expiry = datetime.combine(date.fromisoformat(raw), datetime.max.time())
        if datetime.now() > expiry:
            return jsonify({'error': 'license expired'}), 403
    except Exception:
        return jsonify({'error': 'server error'}), 500

    # Device lock (skip for exempt keys)
    if key not in exempt and deviceId:
        registered_list = devices.get(key, [])
        # Migrate old string format
        migrated = []
        for d in (registered_list if isinstance(registered_list, list) else [registered_list] if registered_list else []):
            if isinstance(d, str):
                migrated.append({'id': d, 'registered_at': 'Unknown'})
            else:
                migrated.append(d)
        registered_list = migrated
        limit       = int(entry.get('device_limit', 1))
        existing_ids = [d['id'] for d in registered_list]

        # Check if device was reset — force logout
        with _revoked_devices_lock:
            if deviceId in _revoked_devices:
                _revoked_devices.discard(deviceId)
                return jsonify({'error': 'device_reset'}), 403

        if deviceId in existing_ids:
            # Update last_seen
            now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            for d in registered_list:
                if d['id'] == deviceId:
                    d['last_seen'] = now_str
                    break
            devices[key] = registered_list
            _save_all(licenses, devices, exempt)
        elif len(registered_list) < limit:
            now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            registered_list.append({'id': deviceId, 'registered_at': now_str, 'last_seen': now_str})
            devices[key] = registered_list
            _save_all(licenses, devices, exempt)
        else:
            return jsonify({'error': 'Maximum device limit exceeded. This license is already active on ' + str(limit) + ' device(s). Contact +94 77 831 5058 to transfer.'}), 403

    with _cookies_lock:
        cookies = _cookies_netscape

    return jsonify({
        'licenses':         {key: entry},
        'cookies_netscape': cookies,
    })

# ─── ADMIN: set cookies ───────────────────────────────────────────────────────
@app.route('/admin/set-cookies', methods=['POST'])
def set_cookies():
    if not _auth(request):
        return jsonify({'error': 'unauthorized'}), 401
    body = request.get_json(silent=True) or {}
    text = body.get('cookies', '').strip()
    if not text:
        return jsonify({'error': 'cookies field required'}), 400
    ok = _save_cookies(text)
    if ok:
        return jsonify({'success': True, 'length': len(text)})
    return jsonify({'error': 'failed to save'}), 500

# ─── ADMIN: get cookies ───────────────────────────────────────────────────────
@app.route('/admin/get-cookies', methods=['GET'])
def get_cookies():
    if not _auth(request):
        return jsonify({'error': 'unauthorized'}), 401
    with _cookies_lock:
        cookies = _cookies_netscape
    return jsonify({'cookies_netscape': cookies, 'length': len(cookies)})

# ─── ADMIN: reset device lock ─────────────────────────────────────────────────
@app.route('/admin/reset-device', methods=['GET'])
def reset_device():
    if not _auth(request):
        return jsonify({'error': 'unauthorized'}), 401
    key = request.args.get('key', '').strip().upper()
    licenses, devices, exempt = _load_all()
    if key not in licenses:
        return jsonify({'error': 'key not found'}), 404
    removed = devices.pop(key, None)
    _save_all(licenses, devices, exempt)
    if removed:
        removed_list = removed if isinstance(removed, list) else [removed]
        with _revoked_devices_lock:
            for d in removed_list:
                dev_id = d['id'] if isinstance(d, dict) else d
                _revoked_devices.add(dev_id)
        count = len(removed_list)
        msg = 'Device lock cleared for ' + key + ' (' + str(count) + ' device(s) will be logged out)'
    else:
        count = 0
        msg = 'No devices registered for that key'
    return jsonify({'success': True, 'message': msg, 'revoked_count': count})

# ─── ADMIN: list all devices ──────────────────────────────────────────────────
@app.route('/admin/devices', methods=['GET'])
def list_devices():
    if not _auth(request):
        return jsonify({'error': 'unauthorized'}), 401
    _, devices, _ = _load_all()
    return jsonify(devices)

# ─── ADMIN: list all licenses ─────────────────────────────────────────────────
@app.route('/admin/licenses', methods=['GET'])
def list_licenses():
    if not _auth(request):
        return jsonify({'error': 'unauthorized'}), 401
    licenses, devices, exempt = _load_all()
    return jsonify({'licenses': licenses, 'devices': devices, 'exempt': exempt})

# ─── ADMIN: add license ───────────────────────────────────────────────────────
@app.route('/admin/add-license', methods=['POST'])
def add_license():
    if not _auth(request):
        return jsonify({'error': 'unauthorized'}), 401
    body    = request.get_json(silent=True) or {}
    key     = body.get('key', '').strip().upper()
    expires = body.get('expires', '').strip()
    plan    = body.get('plan', 'sg')
    if not key or not expires:
        return jsonify({'error': 'key and expires required'}), 400
    licenses, devices, exempt = _load_all()
    if key in licenses:
        return jsonify({'error': 'key already exists', 'expires': licenses[key]['expires']}), 409
    licenses[key] = {'expires': expires, 'plan': plan, 'device_limit': 1}
    _save_all(licenses, devices, exempt)
    return jsonify({'success': True, 'key': key, 'expires': expires})

# ─── ADMIN: revoke license ────────────────────────────────────────────────────
@app.route('/admin/revoke-license', methods=['POST'])
def revoke_license():
    if not _auth(request):
        return jsonify({'error': 'unauthorized'}), 401
    body = request.get_json(silent=True) or {}
    key  = body.get('key', '').strip().upper()
    licenses, devices, exempt = _load_all()
    if key not in licenses:
        return jsonify({'error': 'key not found'}), 404
    del licenses[key]
    devices.pop(key, None)
    if key in exempt:
        exempt.remove(key)
    _save_all(licenses, devices, exempt)
    return jsonify({'success': True})

# ─── ADMIN: extend license ────────────────────────────────────────────────────
@app.route('/admin/extend-license', methods=['POST'])
def extend_license():
    if not _auth(request):
        return jsonify({'error': 'unauthorized'}), 401
    body  = request.get_json(silent=True) or {}
    key   = body.get('key', '').strip().upper()
    licenses, devices, exempt = _load_all()
    if key not in licenses:
        return jsonify({'error': 'key not found'}), 404
    try:
        hours = int(body.get('hours', int(body.get('days', 0)) * 24))
        raw   = licenses[key]['expires']
        try:
            cur = datetime.fromisoformat(raw)
        except Exception:
            cur = datetime.combine(date.fromisoformat(raw), datetime.min.time())
        base    = max(cur, datetime.now())
        new_exp = (base + timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S')
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    licenses[key]['expires'] = new_exp
    _save_all(licenses, devices, exempt)
    return jsonify({'success': True, 'expires': new_exp})

# ─── ADMIN: reduce license ────────────────────────────────────────────────────
@app.route('/admin/reduce-license', methods=['POST'])
def reduce_license():
    if not _auth(request):
        return jsonify({'error': 'unauthorized'}), 401
    body  = request.get_json(silent=True) or {}
    key   = body.get('key', '').strip().upper()
    licenses, devices, exempt = _load_all()
    if key not in licenses:
        return jsonify({'error': 'key not found'}), 404
    try:
        hours   = int(body.get('hours', int(body.get('days', 0)) * 24))
        raw     = licenses[key]['expires']
        try:
            cur = datetime.fromisoformat(raw)
        except Exception:
            cur = datetime.combine(date.fromisoformat(raw), datetime.min.time())
        new_exp = (cur - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S')
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    licenses[key]['expires'] = new_exp
    _save_all(licenses, devices, exempt)
    return jsonify({'success': True, 'expires': new_exp})

# ─── ADMIN: set device limit ─────────────────────────────────────────────────
@app.route('/admin/set-device-limit', methods=['POST'])
def set_device_limit():
    if not _auth(request):
        return jsonify({'error': 'unauthorized'}), 401
    import random
    body  = request.get_json(silent=True) or {}
    key   = body.get('key', '').strip().upper()
    limit = body.get('limit', 1)
    try:
        limit = int(limit)
        if limit < 1:
            raise ValueError
    except Exception:
        return jsonify({'error': 'limit must be a positive integer'}), 400
    licenses, devices, exempt = _load_all()
    if key not in licenses:
        return jsonify({'error': 'key not found'}), 404
    old_limit = int(licenses[key].get('device_limit', 1))
    licenses[key]['device_limit'] = limit
    dev_list = devices.get(key, [])
    if isinstance(dev_list, str):
        dev_list = [{'id': dev_list, 'registered_at': 'Unknown'}]
    elif isinstance(dev_list, list):
        dev_list = [d if isinstance(d, dict) else {'id': d, 'registered_at': 'Unknown'} for d in dev_list]
    purged = []
    kept   = dev_list
    if len(dev_list) > limit:
        random.shuffle(dev_list)
        kept   = dev_list[:limit]
        purged = [d['id'] for d in dev_list[limit:]]
        devices[key] = kept
    _save_all(licenses, devices, exempt)
    return jsonify({
        'success':      True,
        'key':          key,
        'old_limit':    old_limit,
        'new_limit':    limit,
        'kept':         kept,
        'purged':       purged,
        'purged_count': len(purged),
    })

# ─── ADMIN: check license ─────────────────────────────────────────────────────
@app.route('/admin/check-license', methods=['GET'])
def check_license():
    if not _auth(request):
        return jsonify({'error': 'unauthorized'}), 401
    key = request.args.get('key', '').strip().upper()
    licenses, devices, exempt = _load_all()
    if key not in licenses:
        return jsonify({'error': 'key not found'}), 404
    dev = devices.get(key, [])
    if isinstance(dev, str):
        dev = [{'id': dev, 'registered_at': 'Unknown', 'last_seen': None}]
    elif isinstance(dev, list):
        dev = [d if isinstance(d, dict) else {'id': d, 'registered_at': 'Unknown', 'last_seen': None} for d in dev]
    now = datetime.now(timezone.utc)
    for d in dev:
        ls = d.get('last_seen')
        if ls:
            try:
                last = datetime.fromisoformat(ls.replace('Z', '+00:00'))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                d['active'] = (now - last).total_seconds() < 600
            except Exception:
                d['active'] = False
        else:
            d['active'] = False
    return jsonify({
        'entry':        licenses[key],
        'devices':      dev,
        'device_count': len(dev),
        'device_limit': int(licenses[key].get('device_limit', 1)),
        'exempt':       key in exempt,
    })

# ─── DEBUG ────────────────────────────────────────────────────────────────────
@app.route('/debug', methods=['GET'])
def debug():
    if not _auth(request):
        return jsonify({'error': 'unauthorized'}), 401
    licenses, devices, exempt = _load_all()
    with _cookies_lock:
        cookies_len = len(_cookies_netscape)
    return jsonify({
        'DATA_DIR':       DATA_DIR,
        'LICENSE_FILE':   LICENSE_FILE,
        'COOKIES_FILE':   COOKIES_FILE,
        'file_exists':    os.path.exists(LICENSE_FILE),
        'cookies_exists': os.path.exists(COOKIES_FILE),
        'cookies_length': cookies_len,
        'data_dir_files': os.listdir(DATA_DIR) if os.path.isdir(DATA_DIR) else [],
        'license_count':  len(licenses),
        'license_keys':   list(licenses.keys()),
        'device_counts':  {k: len(v) if isinstance(v, list) else 1 for k, v in devices.items()},
        'exempt':         exempt,
    })

# ─── INDEX ────────────────────────────────────────────────────────────────────
@app.route('/', methods=['GET'])
def index():
    return jsonify({'status': 'SGD Access Server running'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

import os
import json
import uuid
import pytz
from flask import Flask, request, jsonify
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
ADMIN_TOKEN = os.environ.get("SGD_ADMIN_TOKEN", "changeme-admin-token")
DATA_FILE = "sgd_licenses.json"
SL_TZ = pytz.timezone("Asia/Colombo")

# ─── SGD Cookies (update via !sgd setcookies) ─────────────────────────────────
SGD_COOKIES = [
    {"domain": "signaturedigital.asia", "name": "__cflb", "value": "0H8vYnwA1MLpqRzKld2a7mXQPsk9VjBNwGy9C4mfs37b", "path": "/", "secure": True, "httpOnly": True, "expirationDate": 1781375342},
    {"domain": "signaturedigital.asia", "name": "SGD-hlib", "value": "true", "path": "/", "secure": False, "httpOnly": False, "expirationDate": 1781375342},
    {"domain": ".signaturedigital.asia", "name": "SGD-sc", "value": "0gAAAAABqLE9GYzJLjZtk0gty7KptlIVg8T5DUQmgGVk37ctQxDeR0k0Qh_9dfUpcQx_zKWkT0-5mSkFXdoWgku1Tk9Ka9dcbuTahOumIublmWhKAxn9uANLjhfwHtdASfS9I5EONtXt3I7gzZBFbkLTYgARcAZyQe7kkwTyIdQG-EaC8nNS6evUmU1nSqdO_awYEE_sjn6nSlbieO6irsHiJD9jRhA8SK02qPTF8n_h0PH4grk2jCZk", "path": "/", "secure": True, "httpOnly": False, "expirationDate": 1781375342},
    {"domain": "signaturedigital.asia", "name": "SGD-gn", "value": "User", "path": "/", "secure": False, "httpOnly": False, "expirationDate": 1781375342},
    {"domain": "signaturedigital.asia", "name": "SGD_session_active", "value": "true", "path": "/", "secure": False, "httpOnly": False, "expirationDate": 1781375342},
    {"domain": ".signaturedigital.asia", "name": "__Secure-SGD-is", "value": "ois1.eyJ2IjoxLCJhbGciOiJBMjU2R0NNIiwia2lkIjoiU0dELXdlYi12MSJ9.BoXyEmItRDQXA-Gk.HO3oUzP2dP_tZrmrIFhzCCnhDLkJsebDchicpe1LFv5rdmBZVzzSSY56uqQui2Qs9HBUAt2RYyjN2HcvK65wKBU2dXMiZfAeDvvWZDxnwQQagR5hbtNo-jH56hvuzw6H-_K_CeXwrOEmsTvJZEu_q7vT6FkUKTs_eRGmlnndDBjXaRHMerRPgT2sGmyR7d0WmrZPOCXoxLrnQdgWb1My6iGnf9ClNwcMNYoCQ217FX0gjN2uBL9JHwVbfeiJQhel1ZdUUfQwUWHv8amoh3-IdwF0TV2qp-coWWs1SCtz5137KNQKS6ecrbqM3p98-lhjRCtlyxndVqDIKpm1AtTxBgYb86LudHjgIunDAkV5Y0aZq-bx_EZML8m-ubYy9xN3X96gSphBcg_Ec53GGFGi3CVx2wJJz8jYBFs635dWGZtJEBFSVg-GBey9MfbZae07d2DB0oqWaC6eE36_VP8xaAfkOaWpHU7qiFrUi9kQ6QkRtFQoBn9szKbu5WHhj6bX-gGkQH-5G3EebKIl8wMiaP1zNwhd4P42tE0Tf9WDQpMo0eHyrWMp-PBJiMJi6kSeqAEW6E7foOWXBxmsdyRX2LHI0XmWnhJEHdsk6IZN_hsG3QJdgJfjVo5Cd-SVnthaGIXp8L1F4cmfXfZh-Ywqo3gTs1B5RvNUaTCUjjUSNDgcIqyH7wtrDVYforWqBmSsx57dF20ji5YcRvuJgZgcZh9BCWpDApGCUwgUPfP4gTJqAo_tslHz34d0Ee4QFQeWLsCMtpZUDvZNaQl520Cguw_KSbYX7XnSfDXNtgHi-G2s0EIcznrWuyZE3D81kwq_pOavX67_m6iP6NUCpS2WIt37W7cxuQMZHwzOS6W0mX8XM7jiVB1cPeG--GHZQd2UtHPRaEC85WNk0IJEVoU8nssIHXRrMR6uVN7B0mgp2wKhxb6tGDH92oIo2UP5QOaQHmTwm8CQSUWLAd1H3Izm-eGI0VXsHc8qry8UcBjv3dRQfbYehxckZbnWAnMqvXC70InDcma41hEPoLGw-TGkL_Gc2Wvlj_PhXi8u9rVKo7CAjZDVnZQBQlX-8L5uWfZDR9eOIFud2IniEe9Rc7tln3uqJAS08FuYArjKMIQktlu6qf_K5XztKNOy26t3pTrziD31PsWWYDjy2d4tRYU3SKAQ3keeOpQ8uB6trHeqq6SvFPxAaei8HnE23p5CFoHiG086x9DwTnePmKWQvYrrLCqTv_cU0bsPfyOBDSPNV7Pu4VFzgwwcBEMWZqR-8_LOZYbguklT2twwbnwmn99DJnZnHeIqz7QCAW2d607lFHRAgBIB_o17jAbcC577_wHjx36ZPFJQjS1UfocmzSrzIl169lY8lcFRZTprDMu-Qy_TUwQ-nS5_i83bvEbi3EFCbn8rHG1ipOAOLI3wEJzkUnUIsXunBUGZyHKLSpZEhEIQDFak-QWa4cxVxD_oCng9SpSEr-b-KpYwXQlZPR9yabwcL5fzc3nw9wktutyKtYWhFODPKOKOJNI8bi45xbaHcRtm", "path": "/", "secure": True, "httpOnly": True, "expirationDate": 1781375342},
    {"domain": "signaturedigital.asia", "name": "SGD-client-auth-info", "value": "%7B%22isOptedOut%22%3Afalse%2C%22loggedInWithOAuth%22%3Atrue%2C%22user%22%3A%7B%22name%22%3A%22Anonymous%20User%22%2C%22id%22%3A%22usr_4k1v98xPldsm%22%2C%22connectionType%22%3A1%7D%7D", "path": "/", "secure": False, "httpOnly": False, "expirationDate": 1781375342},
    {"domain": ".signaturedigital.asia", "name": "SGD-did", "value": "14e082b5-7d95-504d-9d32-fbc55b55eb21", "path": "/", "secure": False, "httpOnly": False, "expirationDate": 1781375342},
]

# ─── JSON Storage ─────────────────────────────────────────────────────────────
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"licenses": {}, "cookies": SGD_COOKIES}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def sl_now():
    return datetime.now(SL_TZ)

def sl_isoformat(dt):
    return dt.isoformat()

# ─── Admin Auth Decorator ─────────────────────────────────────────────────────
def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("X-Admin-Token")
        if token != ADMIN_TOKEN:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "SGD Access Server running"})

# Validate license key (called by extension)
@app.route("/validate", methods=["POST"])
def validate():
    body = request.get_json()
    license_key = body.get("license_key", "").strip().upper()
    device_id = body.get("device_id", "").strip()

    if not license_key or not device_id:
        return jsonify({"success": False, "error": "Missing license_key or device_id"})

    data = load_data()
    license = data["licenses"].get(license_key)

    if not license:
        return jsonify({"success": False, "error": "Invalid license key"})

    if license.get("status") != "active":
        return jsonify({"success": False, "error": "License is revoked"})

    # Check expiry
    expiry = datetime.fromisoformat(license["expiry"])
    if sl_now() > expiry:
        data["licenses"][license_key]["status"] = "expired"
        save_data(data)
        return jsonify({"success": False, "error": "License has expired"})

    # Device lock
    if license.get("device_id"):
        if license["device_id"] != device_id:
            return jsonify({"success": False, "error": "Device mismatch. Contact support to reset."})
    else:
        # First use — bind device
        data["licenses"][license_key]["device_id"] = device_id
        save_data(data)

    return jsonify({
        "success": True,
        "expiry": license["expiry"],
        "phone": license.get("phone", ""),
    })

# Serve cookies (called by extension after validation)
@app.route("/cookies", methods=["POST"])
def get_cookies():
    body = request.get_json()
    license_key = body.get("license_key", "").strip().upper()
    device_id = body.get("device_id", "").strip()

    if not license_key or not device_id:
        return jsonify({"success": False, "error": "Missing fields"})

    data = load_data()
    license = data["licenses"].get(license_key)

    if not license or license.get("status") != "active":
        return jsonify({"success": False, "error": "Invalid or inactive license"})

    # Verify device
    if license.get("device_id") and license["device_id"] != device_id:
        return jsonify({"success": False, "error": "Device mismatch"})

    return jsonify({
        "success": True,
        "cookies": data.get("cookies", SGD_COOKIES)
    })

# ─── Admin Routes ─────────────────────────────────────────────────────────────

@app.route("/admin/add", methods=["POST"])
@require_admin
def admin_add():
    body = request.get_json()
    phone = body.get("phone", "").strip()
    days = int(body.get("days", 30))

    if not phone:
        return jsonify({"success": False, "error": "Phone required"})

    license_key = "SGD-" + uuid.uuid4().hex[:4].upper() + "-" + uuid.uuid4().hex[:4].upper() + "-" + uuid.uuid4().hex[:4].upper()
    expiry = sl_now() + timedelta(days=days)

    data = load_data()
    data["licenses"][license_key] = {
        "phone": phone,
        "status": "active",
        "expiry": sl_isoformat(expiry),
        "device_id": None,
        "created": sl_isoformat(sl_now()),
    }
    save_data(data)

    return jsonify({"success": True, "license_key": license_key, "expiry": sl_isoformat(expiry)})

@app.route("/admin/revoke", methods=["POST"])
@require_admin
def admin_revoke():
    body = request.get_json()
    license_key = body.get("license_key", "").strip().upper()
    data = load_data()

    if license_key not in data["licenses"]:
        return jsonify({"success": False, "error": "License not found"})

    data["licenses"][license_key]["status"] = "revoked"
    save_data(data)
    return jsonify({"success": True, "message": f"{license_key} revoked"})

@app.route("/admin/extend", methods=["POST"])
@require_admin
def admin_extend():
    body = request.get_json()
    license_key = body.get("license_key", "").strip().upper()
    days = int(body.get("days", 30))
    data = load_data()

    if license_key not in data["licenses"]:
        return jsonify({"success": False, "error": "License not found"})

    current_expiry = datetime.fromisoformat(data["licenses"][license_key]["expiry"])
    base = max(current_expiry, sl_now())
    new_expiry = base + timedelta(days=days)
    data["licenses"][license_key]["expiry"] = sl_isoformat(new_expiry)
    data["licenses"][license_key]["status"] = "active"
    save_data(data)

    return jsonify({"success": True, "new_expiry": sl_isoformat(new_expiry)})

@app.route("/admin/check", methods=["POST"])
@require_admin
def admin_check():
    body = request.get_json()
    license_key = body.get("license_key", "").strip().upper()
    data = load_data()

    license = data["licenses"].get(license_key)
    if not license:
        return jsonify({"success": False, "error": "Not found"})

    return jsonify({"success": True, "license": license})

@app.route("/admin/list", methods=["GET"])
@require_admin
def admin_list():
    data = load_data()
    licenses = []
    for key, val in data["licenses"].items():
        licenses.append({**val, "license_key": key})
    licenses.sort(key=lambda x: x.get("created", ""), reverse=True)
    return jsonify({"success": True, "count": len(licenses), "licenses": licenses})

@app.route("/admin/resetdevice", methods=["POST"])
@require_admin
def admin_reset_device():
    body = request.get_json()
    license_key = body.get("license_key", "").strip().upper()
    data = load_data()

    if license_key not in data["licenses"]:
        return jsonify({"success": False, "error": "License not found"})

    data["licenses"][license_key]["device_id"] = None
    save_data(data)
    return jsonify({"success": True, "message": f"Device reset for {license_key}"})

@app.route("/admin/setcookies", methods=["POST"])
@require_admin
def admin_set_cookies():
    body = request.get_json()
    cookies = body.get("cookies")
    if not cookies or not isinstance(cookies, list):
        return jsonify({"success": False, "error": "cookies must be a list"})

    data = load_data()
    data["cookies"] = cookies
    save_data(data)
    return jsonify({"success": True, "message": f"Updated {len(cookies)} cookies"})

@app.route("/admin/getcookies", methods=["GET"])
@require_admin
def admin_get_cookies():
    data = load_data()
    cookies = data.get("cookies", SGD_COOKIES)
    return jsonify({"success": True, "count": len(cookies), "cookies": cookies})

# ─── Run ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

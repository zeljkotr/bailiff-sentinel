"""
app.py — paycheck-sentinel Flask backend.

Run with:
    python app.py
By default it listens on 0.0.0.0:5000 (reachable from other machines on the network).

Developed by Zeljko Tripcevski
"""

import csv
import hmac
import io
import os
import secrets
import time
from functools import wraps
from urllib.parse import urlencode

from flask import (
    Flask, Response, g, jsonify, redirect, render_template, request, send_file,
    send_from_directory, session,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_session import Session
import redis

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from paycheck_sentinel import auth, db
from paycheck_sentinel.licensing import (
    FREE_DOCS_PER_WINDOW,
    FREE_WINDOW_HOURS,
    get_license_status,
    initialize_license_state,
    limit_message,
    reserve_upload_slots,
    save_license_token,
)
from paycheck_sentinel.checks import analyze, analyze_circular_refund, analyze_transfer
from paycheck_sentinel.pdf_export import build_pdf_report
from paycheck_sentinel.xmlparse import XMLParseError, parse_xml_text

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")

os.makedirs(INSTANCE_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB upload limit

# --- Server-side sessions in Redis ---
# Needed for load balancing (Nginx in front of several Gunicorn/Docker
# instances): without it the session would be tied to the one container that
# created it, and the user would lose state when Nginx switches instances.
# REDIS_HOST is read from an environment variable so the same image works
# locally (host='localhost') and on a Docker network (host='redis' via Docker DNS).
app.config["SESSION_TYPE"] = "redis"
app.config["SESSION_REDIS"] = redis.StrictRedis(
    host=os.environ.get("REDIS_HOST", "localhost"),
    port=int(os.environ.get("REDIS_PORT", 6379)),
    db=0,
)
app.config["SESSION_KEY_PREFIX"] = "paycheck-sentinel:"  # changing it only signs users out once
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_USE_SIGNER"] = True  # signs the session cookie ID for extra protection
app.config["SESSION_COOKIE_NAME"] = "paycheck_sentinel_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Set COOKIE_SECURE=1 once the application is served over HTTPS only.
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"
# A session that is idle for longer than this is signed out.
SESSION_IDLE_SECONDS = int(os.environ.get("SESSION_IDLE_MINUTES", "480")) * 60
app.secret_key = os.environ["FLASK_SECRET_KEY"]  # fails at startup if not set

Session(app)

# Behind Nginx, trust the X-Forwarded-* headers (client IP for the audit log, original scheme).
# Enable only when the application is reachable exclusively through the proxy.
if os.environ.get("TRUST_PROXY", "0") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


# --- Authentication ---
# Every page and API call requires a signed-in user, except PUBLIC_ENDPOINTS. Sessions are
# server-side (Redis). State-changing requests must carry the session's CSRF token in the
# X-CSRF-Token header. Users, passwords and the audit log live in paycheck_sentinel/auth.py.
PUBLIC_ENDPOINTS = {"static", "favicon", "healthz", "metrics", "login_page", "api_login"}
# A user who must change the password can only reach these until they do.
PASSWORD_CHANGE_ENDPOINTS = {"account_page", "api_me", "api_change_password", "api_logout"}


def _safe_next(target):
    """Only allow redirects to paths on this site."""
    target = str(target or "")
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/"


def _load_session_user():
    """The signed-in user for this request, or None (expired, disabled or not signed in)."""
    uid = session.get("uid")
    if not uid:
        return None
    now = time.time()
    last_seen = session.get("last_seen", 0)
    if now - last_seen > SESSION_IDLE_SECONDS:
        session.clear()
        return None
    user = auth.get_user(uid)
    if not user or not user["is_active"]:
        session.clear()
        return None
    if now - last_seen > 60:  # avoid a session write on every request
        session["last_seen"] = now
    return user


def _audit(action, detail=""):
    user = g.get("user")
    auth.audit(user["username"] if user else "", action, detail, request.remote_addr or "")


@app.before_request
def authenticate_request():
    endpoint = request.endpoint
    if endpoint is None:  # unknown URL: let the 404 handler answer
        return None

    if endpoint == "metrics":
        # Optional bearer token for Prometheus; without METRICS_TOKEN the route stays open
        # (restrict it in Nginx or on the network instead).
        token = os.environ.get("METRICS_TOKEN", "")
        if token and not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {token}"):
            return Response("Unauthorized", status=401)
        return None
    if endpoint in PUBLIC_ENDPOINTS:
        return None

    user = _load_session_user()
    if user is None:
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required.", "code": "auth_required"}), 401
        target = request.full_path if request.query_string else request.path
        return redirect("/login?" + urlencode({"next": target}))
    g.user = user

    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        expected = session.get("csrf", "")
        sent = request.headers.get("X-CSRF-Token", "")
        if not expected or not hmac.compare_digest(sent, expected):
            return jsonify({"error": "Invalid or missing CSRF token. Reload the page.", "code": "csrf"}), 403

    if user["must_change_password"] and endpoint not in PASSWORD_CHANGE_ENDPOINTS:
        if request.path.startswith("/api/"):
            return jsonify({"error": "You must change your password first.",
                            "code": "password_change_required"}), 403
        return redirect("/account?force=1")
    return None


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.user["role"] != "admin":
            return jsonify({"error": "Administrator rights are required."}), 403
        return view(*args, **kwargs)
    return wrapper


@app.route("/healthz")
def healthz():
    try:
        with db.get_db() as conn:
            conn.execute("SELECT 1")
    except Exception:
        return jsonify({"status": "error"}), 503
    return jsonify({"status": "ok"})


@app.route("/login")
def login_page():
    if _load_session_user() is not None:
        return redirect(_safe_next(request.args.get("next")))
    return render_template("login.html", no_users=auth.count_users() == 0)


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    body = request.get_json(silent=True) or {}
    user, reason = auth.authenticate(body.get("username", ""), body.get("password", ""))
    ip = request.remote_addr or ""
    if user is None:
        auth.audit(auth.normalize_username(body.get("username", "")), "login_failed", reason, ip)
        # The same message for every reason, so it does not reveal which usernames exist.
        return jsonify({"error": "Invalid username or password, or the account is temporarily locked."}), 401

    session.clear()
    session["uid"] = user["id"]
    session["csrf"] = secrets.token_urlsafe(32)
    session["last_seen"] = time.time()
    auth.audit(user["username"], "login", "", ip)
    return jsonify({
        "ok": True,
        "next": _safe_next(body.get("next")),
        "must_change_password": user["must_change_password"],
    })


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    _audit("logout")
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/me", methods=["GET"])
def api_me():
    return jsonify({"user": g.user, "csrf": session.get("csrf", "")})


@app.route("/api/auth/change_password", methods=["POST"])
def api_change_password():
    body = request.get_json(silent=True) or {}
    current = body.get("current_password", "")
    new = body.get("new_password", "")
    _, reason = auth.authenticate(g.user["username"], current)
    if reason != "ok":
        return jsonify({"error": "The current password is incorrect."}), 400
    if new == current:
        return jsonify({"error": "The new password must be different from the current one."}), 400
    try:
        auth.set_password(g.user["id"], new, must_change_password=False)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    session["csrf"] = secrets.token_urlsafe(32)
    _audit("password_changed")
    return jsonify({"ok": True})


@app.route("/account")
def account_page():
    return render_template(
        "account.html",
        current_user=g.user,
        csrf_token=session.get("csrf", ""),
        force=request.args.get("force") == "1" or g.user["must_change_password"],
    )


@app.route("/api/users", methods=["GET"])
@admin_required
def api_list_users():
    return jsonify({"users": auth.list_users()})


@app.route("/api/users", methods=["POST"])
@admin_required
def api_create_user():
    body = request.get_json(silent=True) or {}
    try:
        user = auth.create_user(body.get("username", ""), body.get("password", ""),
                                body.get("role", "user"), must_change_password=True)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    _audit("user_created", f"{user['username']} ({user['role']})")
    return jsonify({"user": user}), 201


@app.route("/api/users/<int:user_id>/update", methods=["POST"])
@admin_required
def api_update_user(user_id):
    body = request.get_json(silent=True) or {}
    if user_id == g.user["id"] and body.get("is_active") is False:
        return jsonify({"error": "You cannot disable your own account."}), 400
    try:
        user = auth.update_user(user_id, role=body.get("role"), is_active=body.get("is_active"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    _audit("user_updated", f"{user['username']}: role={user['role']}, active={user['is_active']}")
    return jsonify({"user": user})


@app.route("/api/users/<int:user_id>/reset_password", methods=["POST"])
@admin_required
def api_reset_password(user_id):
    body = request.get_json(silent=True) or {}
    target = auth.get_user(user_id)
    if not target:
        return jsonify({"error": "User not found."}), 404
    try:
        auth.set_password(user_id, body.get("password", ""), must_change_password=True)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    _audit("password_reset", target["username"])
    return jsonify({"ok": True})


@app.route("/api/audit", methods=["GET"])
@admin_required
def api_audit():
    return jsonify({"entries": auth.recent_audit(request.args.get("limit", 100, type=int))})


# --- Licensing ---
# There is no global license gate: without a commercial license the application runs in free
# mode, limited to FREE_DOCS_PER_WINDOW XML documents per rolling FREE_WINDOW_HOURS hours.
# The limit is enforced in /api/upload (see paycheck_sentinel/licensing.py).

# --- Prometheus application (business) metrics ---
# These are metrics about WHAT THE APPLICATION DOES, not about the server/infrastructure
# (for infrastructure - CPU/RAM/disk - we use node_exporter, separately).

xml_files_parsed_total = Counter(
    "paycheck_sentinel_xml_files_parsed_total",
    "Number of processed XML files, by parsing status",
    ["status"],  # "success", "failure" or "rejected_quota" (free version limit)
)

anomalies_detected_total = Counter(
    "paycheck_sentinel_anomalies_detected_total",
    "Number of detected anomalies, by type and analysis mode",
    ["anomaly_type", "mode"],
)

upload_processing_seconds = Histogram(
    "paycheck_sentinel_upload_processing_seconds",
    "Processing time of /api/upload requests (XML parsing) in seconds",
)

analysis_processing_seconds = Histogram(
    "paycheck_sentinel_analysis_processing_seconds",
    "Processing time of the analysis (analyze/analyze_bank/analyze_transfer) in seconds",
    ["mode"],
)


def get_conn():
    if "db_conn" not in g:
        g.db_conn = db.get_db()
    return g.db_conn


@app.teardown_appcontext
def close_conn(exception=None):
    conn = g.pop("db_conn", None)
    if conn is not None:
        conn.close()


@app.errorhandler(413)
def handle_too_large(e):
    return jsonify({"error": "The file(s) are too large to upload (the limit is 200 MB in total)."}), 413


@app.errorhandler(404)
def handle_not_found(e):
    return jsonify({"error": "The requested resource was not found."}), 404


@app.errorhandler(500)
def handle_server_error(e):
    return jsonify({"error": f"Server error: {e}"}), 500


@app.errorhandler(Exception)
def handle_any_error(e):
    # make sure the API ALWAYS returns JSON, never an HTML error page,
    # so the frontend does not break on "Unexpected token '<'"
    app.logger.exception("Unhandled error")
    return jsonify({"error": f"Unexpected server error: {type(e).__name__}: {e}"}), 500


@app.context_processor
def inject_versioned_static():
    def versioned_static(filename):
        path = os.path.join(app.static_folder, filename)
        try:
            v = int(os.path.getmtime(path))
        except OSError:
            v = int(time.time())
        return f"/static/{filename}?v={v}"
    return dict(versioned_static=versioned_static)


@app.route("/")
def index():
    return render_template(
        "index.html",
        license_status=get_license_status(),
        current_user=g.user,
        csrf_token=session.get("csrf", ""),
    )


@app.route("/favicon.ico")
def favicon():
    # Browsers request /favicon.ico on their own, ignoring the <link> tags in some cases.
    return send_from_directory(
        app.static_folder, "favicon.ico", mimetype="image/x-icon", max_age=86400
    )


@app.route("/api/license/status", methods=["GET"])
def license_status():
    return jsonify(get_license_status())


@app.route("/api/license/activate", methods=["POST"])
@admin_required
def activate_license():
    body = request.get_json(silent=True) or {}
    token = body.get("token", "")
    if not isinstance(token, str) or not token.strip():
        return jsonify({"ok": False, "error": "Enter the activation key."}), 400

    try:
        result = save_license_token(token.strip())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    _audit("license_activated", result["licensee"])
    return jsonify({
        "ok": True,
        "licensee": result["licensee"],
        "expires": result["expires"],
    })


@app.route("/metrics")
def metrics():
    # Prometheus scrapes this route (every 15s by default, per our prometheus.yml configuration)
    # and reads all the Counter/Histogram metrics defined above in this file.
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.route("/api/batches", methods=["GET"])
def api_list_batches():
    conn = get_conn()
    batches = db.list_batches(conn)
    for b in batches:
        b["file_names"] = _json_or_empty(b["file_names"])
        b["columns_json"] = _json_or_empty(b["columns_json"])
    return jsonify(batches)


@app.route("/api/batches/<int:batch_id>", methods=["GET"])
def api_get_batch(batch_id):
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch not found."}), 404
    batch["file_names"] = _json_or_empty(batch["file_names"])
    batch["columns_json"] = _json_or_empty(batch["columns_json"])
    return jsonify(batch)


@app.route("/api/batches/<int:batch_id>", methods=["DELETE"])
@admin_required
def api_delete_batch(batch_id):
    conn = get_conn()
    db.delete_batch(conn, batch_id)
    _audit("batch_deleted", f"batch {batch_id}")
    return jsonify({"ok": True})


def _decode_xml_bytes(raw_bytes):
    """Tries to decode XML bytes by recognizing the BOM (UTF-16 LE/BE, UTF-8),
    with a fallback to UTF-8 and then CP1250 if there is no BOM."""
    if raw_bytes.startswith(b"\xff\xfe"):
        return raw_bytes.decode("utf-16-le")
    if raw_bytes.startswith(b"\xfe\xff"):
        return raw_bytes.decode("utf-16-be")
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        return raw_bytes.decode("utf-8-sig")
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # no BOM, but it may still be UTF-16 without a marker - check the pattern
    # (many null bytes at even/odd positions are a sign of UTF-16 text)
    if raw_bytes[:200].count(b"\x00") > 20:
        try:
            return raw_bytes.decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    return raw_bytes.decode("cp1250", errors="replace")


@app.route("/api/upload", methods=["POST"])
def api_upload():
    upload_start = time.time()
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No file was sent."}), 400

    # Free mode: refuse early, before parsing anything, if the quota is already used up.
    status = get_license_status()
    if status["type"] == "free" and status["remaining"] <= 0:
        xml_files_parsed_total.labels(status="rejected_quota").inc(
            sum(1 for f in files if f.filename.lower().endswith(".xml"))
        )
        return jsonify({"error": limit_message(status), "license": status}), 429

    parsed = []  # (file name, rows, columns, statement owner account)
    errors = []

    for f in files:
        if not f.filename.lower().endswith(".xml"):
            continue
        try:
            raw_bytes = f.read()
            text = _decode_xml_bytes(raw_bytes)
            rows, cols, file_own_account = parse_xml_text(text)
        except XMLParseError as e:
            errors.append(f"{f.filename}: {e}")
            xml_files_parsed_total.labels(status="failure").inc()
            continue
        parsed.append((f.filename, rows, cols, file_own_account))

    # Reserve free-mode slots only for files that parsed successfully, so a broken file
    # does not use up the quota. With a commercial license every file is granted.
    granted = reserve_upload_slots([p[0] for p in parsed]) if parsed else 0
    for name, _rows, _cols, _account in parsed[granted:]:
        errors.append(
            f"{name}: skipped, free version limit reached "
            f"({FREE_DOCS_PER_WINDOW} XML documents per {FREE_WINDOW_HOURS} hours)"
        )
    if granted:
        xml_files_parsed_total.labels(status="success").inc(granted)
    if len(parsed) > granted:
        xml_files_parsed_total.labels(status="rejected_quota").inc(len(parsed) - granted)
    accepted = parsed[:granted]

    combined_rows = []
    combined_columns = []
    seen_cols = set()
    file_names = []
    own_account = None
    own_accounts = []

    for name, rows, cols, file_own_account in accepted:
        file_names.append(name)
        if file_own_account and not own_account:
            own_account = file_own_account
        if file_own_account:
            own_accounts.append({"file": name, "account": file_own_account})

        for c in cols:
            if c not in seen_cols:
                seen_cols.add(c)
                combined_columns.append(c)

        for r in rows:
            r["__source_file"] = name
            r["__own_account"] = file_own_account or ""
        combined_rows.extend(rows)

    if not combined_rows:
        upload_processing_seconds.observe(time.time() - upload_start)
        if parsed:
            # The files were valid, but the free limit was used up in the meantime.
            status = get_license_status()
            return jsonify({
                "error": limit_message(status),
                "license": status,
                "details": errors,
            }), 429
        return jsonify({
            "error": "No rows were loaded successfully.",
            "details": errors,
        }), 400

    label = file_names[0] if len(file_names) == 1 else f"{len(file_names)} files"
    conn = get_conn()
    batch_id = db.create_batch(conn, label, file_names, combined_columns, combined_rows, own_account)

    upload_processing_seconds.observe(time.time() - upload_start)
    _audit("upload", f"batch {batch_id}: {', '.join(file_names)}"[:300])

    return jsonify({
        "batch_id": batch_id,
        "columns": combined_columns,
        "row_count": len(combined_rows),
        "file_names": file_names,
        "errors": errors,
        "own_account": own_account,
        "own_accounts": own_accounts,
        "license": get_license_status(),
    })


@app.route("/api/batches/<int:batch_id>/analyze_bank", methods=["POST"])
def api_analyze_bank(batch_id):
    analyze_start = time.time()
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch not found."}), 404

    body = request.get_json(force=True)
    mapping = {
        "amount_col": body.get("amount_col"),
        "benefit_col": body.get("benefit_col"),
        "ref_col": body.get("ref_col") or None,
        "date_col": body.get("date_col") or None,
    }
    options = {
        "debit_value": body.get("debit_value", "debit"),
        "credit_value": body.get("credit_value", "credit"),
        "max_days_gap": body.get("max_days_gap", 30),
        "require_refnumber": body.get("require_refnumber", False),
    }

    if not mapping["amount_col"] or not mapping["benefit_col"]:
        return jsonify({"error": "You must map the 'Amount' and 'Direction (debit/credit)' columns."}), 400

    raw_rows = [t["raw"] for t in db.get_transactions(conn, batch_id)]
    analyzed = analyze_circular_refund(raw_rows, mapping, options)

    db.save_bank_analysis(conn, batch_id, mapping, options, analyzed)

    refreshed = db.get_transactions(conn, batch_id)
    active = [t for t in refreshed if not t.get("is_false_alarm")]
    total = len(refreshed)
    flagged = [t for t in active if t["flags"]]
    confirmed = [t for t in active if any(f["type"] == "circular_confirmed" for f in t["flags"])]
    possible = [t for t in active if any(f["type"] == "circular_possible" for f in t["flags"])]
    sum_confirmed = sum(t["paid_amount"] or 0 for t in confirmed) / 2  # each pair is counted twice (debit + credit)

    stats = {
        "total": total,
        "flagged_count": len(flagged),
        "confirmed_count": len(confirmed),
        "possible_count": len(possible),
        "confirmed_sum": round(sum_confirmed, 2),
    }

    anomalies_detected_total.labels(anomaly_type="circular_confirmed", mode="bank_statement").inc(len(confirmed))
    anomalies_detected_total.labels(anomaly_type="circular_possible", mode="bank_statement").inc(len(possible))
    analysis_processing_seconds.labels(mode="bank_statement").observe(time.time() - analyze_start)

    return jsonify({"rows": refreshed, "stats": stats})


@app.route("/api/batches/<int:batch_id>/analyze_transfer", methods=["POST"])
def api_analyze_transfer(batch_id):
    analyze_start = time.time()
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch not found."}), 404

    body = request.get_json(force=True)
    mapping = {
        "from_col": body.get("from_col"),
        "to_col": body.get("to_col"),
        "amount_col": body.get("amount_col") or None,
        "date_col": body.get("date_col") or None,
    }
    options = {
        "account_from": body.get("account_from", ""),
        "account_to": body.get("account_to", ""),
    }

    if not mapping["from_col"] or not mapping["to_col"]:
        return jsonify({"error": "You must map the 'Account paying from' and 'Account paying to' columns."}), 400
    if not (options["account_from"].strip() or options["account_to"].strip()):
        return jsonify({"error": "Enter at least one account number (A or B)."}), 400

    raw_rows = [t["raw"] for t in db.get_transactions(conn, batch_id)]
    analyzed = analyze_transfer(raw_rows, mapping, options)

    db.save_transfer_analysis(conn, batch_id, mapping, options, analyzed)

    refreshed = db.get_transactions(conn, batch_id)
    active = [t for t in refreshed if not t.get("is_false_alarm")]
    matched = [t for t in active if t["flags"]]
    total_amount = sum(t["paid_amount"] or 0 for t in matched)

    stats = {
        "total": len(refreshed),
        "flagged_count": len(matched),
        "full_count": len(matched),
        "full_sum": round(total_amount, 2),
    }

    anomalies_detected_total.labels(anomaly_type="transfer", mode="transfer").inc(len(matched))
    analysis_processing_seconds.labels(mode="transfer").observe(time.time() - analyze_start)

    return jsonify({"rows": refreshed, "stats": stats})


@app.route("/api/batches/<int:batch_id>/false_alarm", methods=["POST"])
def api_set_false_alarm(batch_id):
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch not found."}), 404

    body = request.get_json(force=True)
    row_index = body.get("row_index")
    value = body.get("value", True)
    if row_index is None:
        return jsonify({"error": "Missing row_index."}), 400

    db.set_false_alarm(conn, batch_id, row_index, value)
    return jsonify({"ok": True})


@app.route("/api/batches/<int:batch_id>/analyze", methods=["POST"])
def api_analyze(batch_id):
    analyze_start = time.time()
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch not found."}), 404

    body = request.get_json(force=True)
    mapping = {
        "paid_col": body.get("paid_col"),
        "returned_col": body.get("returned_col"),
        "id_col": body.get("id_col") or None,
        "debtor_col": body.get("debtor_col") or None,
        "date_col": body.get("date_col") or None,
    }
    options = {
        "tolerance": body.get("tolerance", 0),
        "outlier_multiplier": body.get("outlier_multiplier", 5),
        "check_full": body.get("check_full", True),
        "check_partial": body.get("check_partial", True),
        "check_dupid": body.get("check_dupid", False),
        "check_duppay": body.get("check_duppay", False),
        "check_outlier": body.get("check_outlier", False),
    }

    if not mapping["paid_col"] or not mapping["returned_col"]:
        return jsonify({"error": "You must map the 'Paid to bank' and 'Bank paid back to me' columns."}), 400

    raw_rows = [t["raw"] for t in db.get_transactions(conn, batch_id)]
    analyzed = analyze(raw_rows, mapping, options)

    db.save_analysis(conn, batch_id, mapping, options, analyzed)

    refreshed = db.get_transactions(conn, batch_id)
    stats = _compute_stats(refreshed)

    anomalies_detected_total.labels(anomaly_type="full", mode="generic").inc(stats["full_count"])
    analysis_processing_seconds.labels(mode="generic").observe(time.time() - analyze_start)

    return jsonify({"rows": refreshed, "stats": stats})


@app.route("/api/batches/<int:batch_id>/results", methods=["GET"])
def api_results(batch_id):
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch not found."}), 404

    txns = db.get_transactions(conn, batch_id)
    stats = _compute_stats(txns)
    batch["file_names"] = _json_or_empty(batch["file_names"])
    batch["columns_json"] = _json_or_empty(batch["columns_json"])
    return jsonify({"batch": batch, "rows": txns, "stats": stats})


@app.route("/api/batches/<int:batch_id>/export.csv", methods=["GET"])
def api_export_csv(batch_id):
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch not found."}), 404

    only_flagged = request.args.get("only_flagged", "0") == "1"
    txns = db.get_transactions(conn, batch_id)
    columns = _json_or_empty(batch["columns_json"])

    if only_flagged:
        txns = [t for t in txns if t["flags"] and not t.get("is_false_alarm")]

    _audit("export_csv", f"batch {batch_id}")
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["SourceFile"] + columns + ["Warnings"])
    for t in txns:
        row = [t["raw"].get("__source_file", "")]
        row += [t["raw"].get(c, "") for c in columns]
        flags_str = "+".join(f["label"] for f in t["flags"]) if t["flags"] else "OK"
        row.append(flags_str)
        writer.writerow(row)

    mem = io.BytesIO(buf.getvalue().encode("utf-8"))
    filename = f"paycheck-sentinel-batch{batch_id}{'-warnings' if only_flagged else ''}.csv"
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=filename)


@app.route("/api/batches/<int:batch_id>/export.pdf", methods=["GET"])
def api_export_pdf(batch_id):
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch not found."}), 404

    only_flagged = request.args.get("only_flagged", "0") == "1"
    only_full = request.args.get("only_full", "0") == "1"
    txns = db.get_transactions(conn, batch_id)
    columns = _json_or_empty(batch["columns_json"])
    mode = batch.get("mode") or "generic"

    full_flag_type = "circular_confirmed" if mode == "bank_statement" else ("transfer" if mode == "transfer" else "full")

    if only_full:
        txns = [t for t in txns if any(f["type"] == full_flag_type for f in t["flags"]) and not t.get("is_false_alarm")]
    elif only_flagged:
        txns = [t for t in txns if t["flags"] and not t.get("is_false_alarm")]

    if mode == "bank_statement":
        all_txns = db.get_transactions(conn, batch_id)
        full_rows = [t for t in all_txns if any(f["type"] == "circular_confirmed" for f in t["flags"]) and not t.get("is_false_alarm")]
        stats = {
            "total": len(all_txns),
            "flagged_count": len([t for t in all_txns if t["flags"] and not t.get("is_false_alarm")]),
            "confirmed_count": len(full_rows),
            "confirmed_sum": round(sum((t["paid_amount"] or 0) for t in full_rows) / 2, 2) if full_rows else 0,
        }
    elif mode == "transfer":
        all_txns = db.get_transactions(conn, batch_id)
        matched = [t for t in all_txns if t["flags"] and not t.get("is_false_alarm")]
        stats = {
            "total": len(all_txns),
            "flagged_count": len(matched),
            "full_count": len(matched),
            "full_sum": round(sum((t["paid_amount"] or 0) for t in matched), 2),
        }
    else:
        stats = _compute_stats(db.get_transactions(conn, batch_id))

    label_suffix = "-full-refund" if only_full else ("-warnings" if only_flagged else "")
    report_kind = "full" if only_full else ("flagged" if only_flagged else "all")
    _audit("export_pdf", f"batch {batch_id}: {report_kind}")
    pdf_buf = build_pdf_report(batch, columns, txns, stats, only_flagged or only_full, mode, report_kind)
    filename = f"paycheck-sentinel-batch{batch_id}{label_suffix}.pdf"
    return send_file(pdf_buf, mimetype="application/pdf", as_attachment=True, download_name=filename)


@app.route("/api/batches/<int:batch_id>/export_filtered.pdf", methods=["POST"])
def api_export_filtered_pdf(batch_id):
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch not found."}), 404

    body = request.get_json(force=True) or {}
    row_indices = set(body.get("row_indices") or [])
    if not row_indices:
        return jsonify({"error": "No rows to export (the filter matches no rows)."}), 400

    requested_columns = body.get("display_columns")
    requested_labels = body.get("column_labels") or {}

    # "Transfer between accounts" search: the client sends report_kind="transfer" plus the
    # search parameters, so the PDF shows exactly what the on-screen table shows.
    is_transfer_export = body.get("report_kind") == "transfer"
    transfer_meta = body.get("transfer_meta") if is_transfer_export else None

    all_txns = db.get_transactions(conn, batch_id)
    columns = _json_or_empty(batch["columns_json"])
    mode = batch.get("mode") or "generic"

    txns = [t for t in all_txns if t["idx"] in row_indices]

    active = [t for t in txns if not t.get("is_false_alarm")]
    full_flag_type = "circular_confirmed" if mode == "bank_statement" else ("transfer" if mode == "transfer" else "full")
    full_rows = [t for t in active if any(f["type"] == full_flag_type for f in t["flags"])]

    stats = {
        "total": len(txns),
        "flagged_count": len([t for t in active if t["flags"]]),
        "full_count": len(full_rows),
        "full_sum": round(sum((t["paid_amount"] or 0) for t in full_rows), 2),
    }
    if mode == "bank_statement":
        stats["confirmed_count"] = stats["full_count"]
        stats["confirmed_sum"] = round(stats["full_sum"] / 2, 2) if full_rows else 0

    display_columns_override = None
    if requested_columns:
        # only allow columns that really exist in the data (or our internal
        # __own_account/__source_file fields), to avoid empty columns in the PDF
        allowed = set(columns) | {
            "__own_account", "__source_file", "__reference",
            "__transfer_from", "__transfer_to", "__transfer_date",
        }
        display_columns_override = [c for c in requested_columns if c in allowed]

    report_kind = "transfer" if is_transfer_export else "filtered"
    _audit("export_pdf", f"batch {batch_id}: {report_kind}")
    pdf_buf = build_pdf_report(
        batch, columns, txns, stats, True, mode, report_kind,
        display_columns_override=display_columns_override,
        column_labels_override=requested_labels,
        transfer_meta=transfer_meta,
    )
    filename = f"paycheck-sentinel-batch{batch_id}-{'transfer' if is_transfer_export else 'filtered'}.pdf"
    return send_file(pdf_buf, mimetype="application/pdf", as_attachment=True, download_name=filename)


def _json_or_empty(s):
    import json
    try:
        return json.loads(s) if s else []
    except (TypeError, ValueError):
        return []


def _compute_stats(txns):
    total = len(txns)
    flagged = [t for t in txns if t["flags"] and not t.get("is_false_alarm")]
    full = [t for t in txns if any(f["type"] == "full" for f in t["flags"]) and not t.get("is_false_alarm")]
    sum_full = sum(t["paid_amount"] or 0 for t in full)
    return {
        "total": total,
        "flagged_count": len(flagged),
        "full_count": len(full),
        "full_sum": round(sum_full, 2),
    }


with app.app_context():
    db.init_db()
    initialize_license_state()
    auth.init_auth_schema()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
"""
app.py — paycheck-sentinel Flask backend.

Pokretanje:
    python app.py
Podrazumevano radi na http://127.0.0.1:5000

Developed by Zeljko Tripcevski
"""

import csv
import io
import os
import time

from flask import Flask, jsonify, render_template, request, send_file, g

from paycheck_sentinel import db
from paycheck_sentinel.checks import analyze, analyze_circular_refund, analyze_transfer
from paycheck_sentinel.pdf_export import build_pdf_report
from paycheck_sentinel.xmlparse import XMLParseError, parse_xml_text

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DB_PATH = os.path.join(INSTANCE_DIR, "paycheck_sentinel.db")

os.makedirs(INSTANCE_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB upload limit


def get_conn():
    if "db_conn" not in g:
        g.db_conn = db.get_db(DB_PATH)
    return g.db_conn


@app.teardown_appcontext
def close_conn(exception=None):
    conn = g.pop("db_conn", None)
    if conn is not None:
        conn.close()


@app.errorhandler(413)
def handle_too_large(e):
    return jsonify({"error": "Fajl(ovi) su preveliki za upload (limit je 200 MB ukupno)."}), 413


@app.errorhandler(404)
def handle_not_found(e):
    return jsonify({"error": "Traženi resurs nije pronađen."}), 404


@app.errorhandler(500)
def handle_server_error(e):
    return jsonify({"error": f"Greška na serveru: {e}"}), 500


@app.errorhandler(Exception)
def handle_any_error(e):
    # osiguravamo da API UVEK vraca JSON, nikad HTML stranicu greske,
    # da frontend ne pukne na "Unexpected token '<'"
    app.logger.exception("Neuhvacena greska")
    return jsonify({"error": f"Neočekivana greška na serveru: {type(e).__name__}: {e}"}), 500


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
    return render_template("index.html")


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
        return jsonify({"error": "Batch nije pronadjen."}), 404
    batch["file_names"] = _json_or_empty(batch["file_names"])
    batch["columns_json"] = _json_or_empty(batch["columns_json"])
    return jsonify(batch)


@app.route("/api/batches/<int:batch_id>", methods=["DELETE"])
def api_delete_batch(batch_id):
    conn = get_conn()
    db.delete_batch(conn, batch_id)
    return jsonify({"ok": True})


def _decode_xml_bytes(raw_bytes):
    """Pokusaj da dekodira XML bajtove prepoznajuci BOM (UTF-16 LE/BE, UTF-8),
    uz fallback na UTF-8 pa CP1250 ako BOM ne postoji."""
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
    # nema BOM-a, ali moguce je da je ipak UTF-16 bez markera - proveri obrazac
    # (mnogo null-bajtova na parnim/neparnim pozicijama je znak UTF-16 teksta)
    if raw_bytes[:200].count(b"\x00") > 20:
        try:
            return raw_bytes.decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    return raw_bytes.decode("cp1250", errors="replace")


@app.route("/api/upload", methods=["POST"])
def api_upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "Nijedan fajl nije poslat."}), 400

    combined_rows = []
    combined_columns = []
    seen_cols = set()
    file_names = []
    errors = []
    own_account = None
    own_accounts = []

    for f in files:
        if not f.filename.lower().endswith(".xml"):
            continue
        file_names.append(f.filename)
        try:
            raw_bytes = f.read()
            text = _decode_xml_bytes(raw_bytes)
            rows, cols, file_own_account = parse_xml_text(text)
            if file_own_account and not own_account:
                own_account = file_own_account
            if file_own_account:
                own_accounts.append({"file": f.filename, "account": file_own_account})
        except XMLParseError as e:
            errors.append(f"{f.filename}: {e}")
            continue

        for c in cols:
            if c not in seen_cols:
                seen_cols.add(c)
                combined_columns.append(c)

        for r in rows:
            r["__source_file"] = f.filename
            r["__own_account"] = file_own_account or ""
        combined_rows.extend(rows)

    if not combined_rows:
        return jsonify({
            "error": "Nijedan red nije uspesno ucitan.",
            "details": errors,
        }), 400

    label = file_names[0] if len(file_names) == 1 else f"{len(file_names)} fajlova"
    conn = get_conn()
    batch_id = db.create_batch(conn, label, file_names, combined_columns, combined_rows, own_account)

    return jsonify({
        "batch_id": batch_id,
        "columns": combined_columns,
        "row_count": len(combined_rows),
        "file_names": file_names,
        "errors": errors,
        "own_account": own_account,
        "own_accounts": own_accounts,
    })


@app.route("/api/batches/<int:batch_id>/analyze_bank", methods=["POST"])
def api_analyze_bank(batch_id):
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch nije pronadjen."}), 404

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
        return jsonify({"error": "Moras da mapiras kolone 'Iznos' i 'Smer (debit/credit)'."}), 400

    raw_rows = [t["raw"] for t in db.get_transactions(conn, batch_id)]
    analyzed = analyze_circular_refund(raw_rows, mapping, options)

    db.save_bank_analysis(conn, batch_id, mapping, options, analyzed)

    refreshed = db.get_transactions(conn, batch_id)
    active = [t for t in refreshed if not t.get("is_false_alarm")]
    total = len(refreshed)
    flagged = [t for t in active if t["flags"]]
    confirmed = [t for t in active if any(f["type"] == "circular_confirmed" for f in t["flags"])]
    possible = [t for t in active if any(f["type"] == "circular_possible" for f in t["flags"])]
    sum_confirmed = sum(t["paid_amount"] or 0 for t in confirmed) / 2  # svaki par broji se 2x (debit+credit)

    stats = {
        "total": total,
        "flagged_count": len(flagged),
        "confirmed_count": len(confirmed),
        "possible_count": len(possible),
        "confirmed_sum": round(sum_confirmed, 2),
    }

    return jsonify({"rows": refreshed, "stats": stats})


@app.route("/api/batches/<int:batch_id>/analyze_transfer", methods=["POST"])
def api_analyze_transfer(batch_id):
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch nije pronadjen."}), 404

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
        return jsonify({"error": "Moraš da mapiraš kolone 'Račun sa kog se plaća' i 'Račun na koji se plaća'."}), 400
    if not (options["account_from"].strip() or options["account_to"].strip()):
        return jsonify({"error": "Unesi bar jedan broj računa (A ili B)."}), 400

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

    return jsonify({"rows": refreshed, "stats": stats})


@app.route("/api/batches/<int:batch_id>/false_alarm", methods=["POST"])
def api_set_false_alarm(batch_id):
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch nije pronadjen."}), 404

    body = request.get_json(force=True)
    row_index = body.get("row_index")
    value = body.get("value", True)
    if row_index is None:
        return jsonify({"error": "Nedostaje row_index."}), 400

    db.set_false_alarm(conn, batch_id, row_index, value)
    return jsonify({"ok": True})


@app.route("/api/batches/<int:batch_id>/analyze", methods=["POST"])
def api_analyze(batch_id):
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch nije pronadjen."}), 404

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
        return jsonify({"error": "Moras da mapiras kolone 'Placeno banci' i 'Banka uplatila meni'."}), 400

    raw_rows = [t["raw"] for t in db.get_transactions(conn, batch_id)]
    analyzed = analyze(raw_rows, mapping, options)

    db.save_analysis(conn, batch_id, mapping, options, analyzed)

    refreshed = db.get_transactions(conn, batch_id)
    stats = _compute_stats(refreshed)
    return jsonify({"rows": refreshed, "stats": stats})


@app.route("/api/batches/<int:batch_id>/results", methods=["GET"])
def api_results(batch_id):
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch nije pronadjen."}), 404

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
        return jsonify({"error": "Batch nije pronadjen."}), 404

    only_flagged = request.args.get("only_flagged", "0") == "1"
    txns = db.get_transactions(conn, batch_id)
    columns = _json_or_empty(batch["columns_json"])

    if only_flagged:
        txns = [t for t in txns if t["flags"] and not t.get("is_false_alarm")]

    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["IzvorniFajl"] + columns + ["Upozorenja"])
    for t in txns:
        row = [t["raw"].get("__source_file", "")]
        row += [t["raw"].get(c, "") for c in columns]
        flags_str = "+".join(f["label"] for f in t["flags"]) if t["flags"] else "OK"
        row.append(flags_str)
        writer.writerow(row)

    mem = io.BytesIO(buf.getvalue().encode("utf-8"))
    filename = f"paycheck-sentinel-batch{batch_id}{'-upozorenja' if only_flagged else ''}.csv"
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=filename)


@app.route("/api/batches/<int:batch_id>/export.pdf", methods=["GET"])
def api_export_pdf(batch_id):
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch nije pronadjen."}), 404

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

    label_suffix = "-pun-povrat" if only_full else ("-upozorenja" if only_flagged else "")
    report_kind = "full" if only_full else ("flagged" if only_flagged else "all")
    pdf_buf = build_pdf_report(batch, columns, txns, stats, only_flagged or only_full, mode, report_kind)
    filename = f"paycheck-sentinel-batch{batch_id}{label_suffix}.pdf"
    return send_file(pdf_buf, mimetype="application/pdf", as_attachment=True, download_name=filename)


@app.route("/api/batches/<int:batch_id>/export_filtered.pdf", methods=["POST"])
def api_export_filtered_pdf(batch_id):
    conn = get_conn()
    batch = db.get_batch(conn, batch_id)
    if not batch:
        return jsonify({"error": "Batch nije pronadjen."}), 404

    body = request.get_json(force=True) or {}
    row_indices = set(body.get("row_indices") or [])
    if not row_indices:
        return jsonify({"error": "Nema redova za izvoz (filter ne pogađa nijedan red)."}), 400

    requested_columns = body.get("display_columns")
    requested_labels = body.get("column_labels") or {}

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
        # dozvoli samo kolone koje stvarno postoje u podacima (ili nase interno
        # __own_account/__source_file polje), da izbegnemo prazne kolone u PDF-u
        allowed = set(columns) | {"__own_account", "__source_file"}
        display_columns_override = [c for c in requested_columns if c in allowed]

    pdf_buf = build_pdf_report(batch, columns, txns, stats, True, mode, "filtered", display_columns_override=display_columns_override, column_labels_override=requested_labels)
    filename = f"paycheck-sentinel-batch{batch_id}-filtrirano.pdf"
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
    db.init_db(DB_PATH)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
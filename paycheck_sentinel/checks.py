"""
checks.py — logika provera nad transakcijama.

Svaka funkcija prima listu transakcija (dict sa 'raw', 'paid_amount', 'returned_amount')
i mapiranje kolona, i dodaje flagove u transakciju['flags'] (list of dict {type, label}).

Developed by Zeljko Tripcevski
"""

import re
import statistics
from datetime import datetime


def parse_amount(text):
    """Parsira iznos iz teksta, podrzava srpski format (1.234,56) i standardni (1234.56)."""
    if text is None:
        return None
    t = str(text).strip()
    if t == "":
        return None
    t = re.sub(r"[^\d,.\-]", "", t)
    if t == "":
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t and "." not in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def normalize_text(s):
    s = (s or "").lower()
    table = str.maketrans("čćšžđ", "ccszd")
    s = s.translate(table)
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def parse_datetime_loose(text):
    """Pokusaj da parsira datum/vreme iz razlicitih formata (ISO, sa/bez vremena)."""
    if not text:
        return None
    t = str(text).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y.", "%d.%m.%Y"):
        try:
            return datetime.strptime(t[:len(fmt.replace("%", "")) + 10], fmt)
        except ValueError:
            continue
    # probaj samo prvih 10 karaktera (YYYY-MM-DD) kao fallback
    try:
        return datetime.strptime(t[:10], "%Y-%m-%d")
    except ValueError:
        return None


def analyze_circular_refund(rows, mapping, options):
    """
    Specificna provera za bankovne izvode (npr. iBank format) gde je jedna
    kolona iznos (trnamt), a smer transakcije (odliv/priliv) je u posebnoj
    koloni ('benefit': debit/credit).

    Otkriva "kruzni povrat": odliv (debit) sa racuna, pa priliv (credit)
    istog iznosa nazad na isti racun (isti izvod = isti vlasnik racuna).

    Uparivanje:
      1. Po 'refnumber' koloni (ako je mapirana) + istom iznosu — pouzdano,
         jer banka cesto koristi isti referentni broj za original i povracaj.
      2. Fallback: po istom iznosu + datumu unutar 'max_days_gap' dana —
         manje pouzdano, oznaceno drugom labelom.

    mapping: amount_col, benefit_col, ref_col (opciono), date_col (opciono)
    options: debit_value (default 'debit'), credit_value (default 'credit'),
             max_days_gap (default 30), require_refnumber (bool)
    """
    amount_col = mapping.get("amount_col")
    benefit_col = mapping.get("benefit_col")
    ref_col = mapping.get("ref_col")
    date_col = mapping.get("date_col")

    debit_value = (options.get("debit_value") or "debit").strip().lower()
    credit_value = (options.get("credit_value") or "credit").strip().lower()
    max_days_gap = float(options.get("max_days_gap", 30) or 30)
    require_refnumber = bool(options.get("require_refnumber", False))

    txns = []
    for idx, raw in enumerate(rows):
        amount = parse_amount(raw.get(amount_col)) if amount_col else None
        benefit = (raw.get(benefit_col) or "").strip().lower() if benefit_col else ""
        ref = (raw.get(ref_col) or "").strip() if ref_col else ""
        date_raw = raw.get(date_col) if date_col else ""
        date_parsed = parse_datetime_loose(date_raw)
        txns.append({
            "idx": idx,
            "raw": raw,
            "amount": amount,
            "benefit": benefit,
            "ref": ref,
            "date": date_parsed,
            "flags": [],
        })

    debits = [t for t in txns if t["benefit"] == debit_value and t["amount"]]
    credits = [t for t in txns if t["benefit"] == credit_value and t["amount"]]

    matched_credit_idx = set()

    # 1. Uparivanje po refnumber + iznos (pouzdano)
    if ref_col:
        for d in debits:
            if not d["ref"]:
                continue
            for c in credits:
                if c["idx"] in matched_credit_idx:
                    continue
                if c["ref"] == d["ref"] and abs(c["amount"] - d["amount"]) < 0.01:
                    d["flags"].append({"type": "circular_confirmed", "label": "POVRAT (ref. broj)"})
                    c["flags"].append({"type": "circular_confirmed", "label": "POVRAT (ref. broj)"})
                    matched_credit_idx.add(c["idx"])
                    break

    # 2. Fallback: samo iznos + blizina datuma (manje pouzdano)
    if not require_refnumber:
        for d in debits:
            if any(f["type"] == "circular_confirmed" for f in d["flags"]):
                continue
            for c in credits:
                if c["idx"] in matched_credit_idx:
                    continue
                if any(f["type"] == "circular_confirmed" for f in c["flags"]):
                    continue
                if abs(c["amount"] - d["amount"]) >= 0.01:
                    continue
                if d["date"] and c["date"]:
                    gap_days = abs((c["date"] - d["date"]).total_seconds()) / 86400.0
                    if gap_days > max_days_gap:
                        continue
                elif date_col:
                    # datum kolona mapirana ali nije parsirana kod jednog od njih - preskoci fallback
                    continue
                d["flags"].append({"type": "circular_possible", "label": "MOGUĆ POVRAT (iznos)"})
                c["flags"].append({"type": "circular_possible", "label": "MOGUĆ POVRAT (iznos)"})
                matched_credit_idx.add(c["idx"])
                break

    return txns


def analyze_transfer(rows, mapping, options):
    """
    Obelezava redove koji predstavljaju transfer sa konkretnog racuna
    (from_col == account_from) na konkretan racun (to_col == account_to).
    Koristi se kad korisnik zeli da izdvoji sve uplate izmedju tacno
    dva odredjena racuna iz velikog XML izvoda.

    mapping: from_col, to_col (obavezni), amount_col, date_col (opciono - za prikaz/sumu)
    options: account_from, account_to — bar jedan mora biti popunjen;
             poredjenje je normalizovano (bez razmaka/crtica, case-insensitive)
             da bi radilo bez obzira na format upisa broja racuna.
    """
    from_col = mapping.get("from_col")
    to_col = mapping.get("to_col")
    amount_col = mapping.get("amount_col")

    account_from = normalize_text(options.get("account_from") or "")
    account_to = normalize_text(options.get("account_to") or "")

    txns = []
    for idx, raw in enumerate(rows):
        amount = parse_amount(raw.get(amount_col)) if amount_col else None
        txns.append({
            "idx": idx,
            "raw": raw,
            "paid_amount": amount,
            "returned_amount": None,
            "flags": [],
        })

    if from_col and to_col and (account_from or account_to):
        for t in txns:
            from_val = normalize_text(t["raw"].get(from_col))
            to_val = normalize_text(t["raw"].get(to_col))
            match_from = (not account_from) or (account_from in from_val)
            match_to = (not account_to) or (account_to in to_val)
            if match_from and match_to:
                t["flags"].append({"type": "transfer", "label": "TRANSFER A→B"})

    return txns


def analyze(rows, mapping, options):
    """
    rows: list[dict] — sirovi redovi (kljucevi su imena kolona iz XML-a, plus '__source_file')
    mapping: dict sa kljucevima paid_col, returned_col, id_col, debtor_col, date_col
             (id_col/debtor_col/date_col mogu biti None ako nisu mapirani)
    options: dict sa kljucevima tolerance, outlier_multiplier,
             check_full, check_partial, check_dupid, check_duppay, check_outlier

    Vraca listu transakcija: [{idx, raw, paid_amount, returned_amount, flags: [...]}]
    """
    paid_col = mapping.get("paid_col")
    returned_col = mapping.get("returned_col")
    id_col = mapping.get("id_col")
    debtor_col = mapping.get("debtor_col")
    date_col = mapping.get("date_col")

    tolerance = float(options.get("tolerance", 0) or 0)
    outlier_multiplier = float(options.get("outlier_multiplier", 5) or 5)

    do_full = bool(options.get("check_full"))
    do_partial = bool(options.get("check_partial"))
    do_dupid = bool(options.get("check_dupid")) and bool(id_col)
    do_duppay = bool(options.get("check_duppay")) and bool(debtor_col) and bool(date_col)
    do_outlier = bool(options.get("check_outlier"))

    txns = []
    for idx, raw in enumerate(rows):
        paid = parse_amount(raw.get(paid_col)) if paid_col else None
        returned = parse_amount(raw.get(returned_col)) if returned_col else None
        txns.append({
            "idx": idx,
            "raw": raw,
            "paid_amount": paid,
            "returned_amount": returned,
            "flags": [],
        })

    # 1. Pun povrat
    if do_full:
        for t in txns:
            p, r = t["paid_amount"], t["returned_amount"]
            if p is not None and r is not None and p != 0 and r != 0:
                if abs(p - r) <= tolerance:
                    t["flags"].append({"type": "full", "label": "PUN POVRAT"})

    # 2. Delimican povrat
    if do_partial:
        for t in txns:
            p, r = t["paid_amount"], t["returned_amount"]
            if p is not None and r is not None and p > 0 and r > 0:
                is_full = abs(p - r) <= tolerance
                if not is_full and r < p:
                    t["flags"].append({"type": "partial", "label": "DELIMIČAN"})

    # 3. Duplikat ID-ja
    if do_dupid:
        groups = {}
        for t in txns:
            key = (t["raw"].get(id_col) or "").strip()
            if key == "":
                continue
            groups.setdefault(key, []).append(t)
        for group in groups.values():
            if len(group) > 1:
                for t in group:
                    t["flags"].append({"type": "dupid", "label": "DUP. ID"})

    # 4. Duplikat uplate (isti duznik + isti iznos + isti datum)
    if do_duppay:
        groups = {}
        for t in txns:
            if t["paid_amount"] is None or t["paid_amount"] == 0:
                continue
            debtor = normalize_text(t["raw"].get(debtor_col))
            date = (t["raw"].get(date_col) or "").strip()
            if not debtor or not date:
                continue
            key = f"{debtor}|{t['paid_amount']:.2f}|{date}"
            groups.setdefault(key, []).append(t)
        for group in groups.values():
            if len(group) > 1:
                for t in group:
                    t["flags"].append({"type": "duppay", "label": "DUP. UPLATA"})

    # 5. Statisticki outlier
    if do_outlier:
        amounts = [t["paid_amount"] for t in txns if t["paid_amount"] and t["paid_amount"] > 0]
        if amounts:
            med = statistics.median(amounts)
            if med > 0:
                for t in txns:
                    if t["paid_amount"] is not None and t["paid_amount"] > med * outlier_multiplier:
                        t["flags"].append({"type": "outlier", "label": "OUTLIER"})

    return txns

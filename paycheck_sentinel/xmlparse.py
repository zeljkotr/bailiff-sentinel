"""
xmlparse.py — auto-detekcija ponavljajucih redova i kolona u XML fajlu.

Ideja: umesto da ocekujemo fiksnu semu, pronadjemo tag koji se najvise puta
ponavlja u dokumentu i koji ima dete-elemente (znaci da nosi kolone podataka).
To tretiramo kao "red" tabele, a njegovi direktni dete-tagovi su kolone.

Developed by Zeljko Tripcevski
"""

import xml.etree.ElementTree as ET
from collections import Counter


class XMLParseError(Exception):
    pass


def _local_tag(tag: str) -> str:
    """Ukloni XML namespace prefiks ako postoji, npr '{ns}Row' -> 'Row'."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _flatten_row(row_el, prefix=""):
    """
    Rekurzivno spljosti red u ravan recnik kolona.
    Ugnjezdeni tagovi dobijaju imena tipa 'payeeaccountinfo.acctid'.
    Ako tag ima i tekst i decu (retko), tekst se ignorise u korist dece.
    """
    result = {}
    for child in row_el:
        name = _local_tag(child.tag)
        key = f"{prefix}{name}" if not prefix else f"{prefix}.{name}"
        if len(list(child)) > 0:
            result.update(_flatten_row(child, key))
        else:
            result[key] = (child.text or "").strip()
    return result


def find_own_account(root):
    """
    Pokusaj da pronadje 'racun vlasnika izvoda' — specificno za iBank izvoze
    (stmtrs/acctid na nivou izvoda, pre liste transakcija). Vraca None ako
    ne prepoznaje ovaj format.
    """
    stmtrs = root.find(".//stmtrs")
    if stmtrs is not None:
        acctid_el = stmtrs.find("acctid")
        if acctid_el is not None and acctid_el.text:
            return acctid_el.text.strip()
    return None


def parse_xml_text(xml_text: str):
    """
    Vraca (rows, columns, own_account):
      rows: list[dict[str, str]] — svaki red kao recnik kolona (ugnjezdeni
            tagovi spljosteni u 'roditelj.dete' notaciju)
      columns: list[str] — redosled kolona po prvom pojavljivanju
      own_account: str | None — racun vlasnika izvoda ako je prepoznat
                    (specificno za iBank format), inace None
    Baca XMLParseError ako fajl nije validan ili ne mozemo da prepoznamo strukturu.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise XMLParseError(f"XML nije validan: {e}")

    own_account = find_own_account(root)

    all_elements = list(root.iter())

    freq = Counter(_local_tag(el.tag) for el in all_elements)

    best_tag = None
    best_count = 0
    for el in all_elements:
        tag = _local_tag(el.tag)
        if freq[tag] > 1 and len(list(el)) > 0:
            if freq[tag] > best_count:
                best_count = freq[tag]
                best_tag = tag

    if best_tag is None:
        raise XMLParseError("Nisam uspeo da prepoznam ponavljajuce redove u XML-u.")

    row_elements = [el for el in all_elements if _local_tag(el.tag) == best_tag]

    columns = []
    seen = set()
    rows = []

    for row_el in row_elements:
        row = _flatten_row(row_el)
        for name in row:
            if name not in seen:
                seen.add(name)
                columns.append(name)
        rows.append(row)

    if not rows:
        raise XMLParseError(f"Pronadjeni su redovi ('{best_tag}') ali nemaju kolone.")

    return rows, columns, own_account

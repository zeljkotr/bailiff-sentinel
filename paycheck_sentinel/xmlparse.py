"""
xmlparse.py — auto-detection of repeating rows and columns in an XML file.

Idea: instead of expecting a fixed schema, we find the tag that repeats the most
in the document and has child elements (meaning it carries data columns).
We treat it as a table "row", and its direct child tags are the columns.

Developed by Zeljko Tripcevski
"""

import xml.etree.ElementTree as ET
from collections import Counter


class XMLParseError(Exception):
    pass


def _local_tag(tag: str) -> str:
    """Removes the XML namespace prefix if present, e.g. '{ns}Row' -> 'Row'."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _flatten_row(row_el, prefix=""):
    """
    Recursively flattens a row into a flat dictionary of columns.
    Nested tags get names like 'payeeaccountinfo.acctid'.
    If a tag has both text and children (rare), the text is ignored in favor of the children.
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
    Tries to find the "statement owner account" — specific to iBank exports
    (stmtrs/acctid at the statement level, before the list of transactions).
    Returns None if this format is not recognized.
    """
    stmtrs = root.find(".//stmtrs")
    if stmtrs is not None:
        acctid_el = stmtrs.find("acctid")
        if acctid_el is not None and acctid_el.text:
            return acctid_el.text.strip()
    return None


def _walk_with_depth(el, depth=0):
    """Recursively walks the tree, yielding (element, depth) pairs."""
    yield el, depth
    for child in el:
        yield from _walk_with_depth(child, depth + 1)


def parse_xml_text(xml_text: str):
    """
    Returns (rows, columns, own_account):
      rows: list[dict[str, str]] — each row as a dictionary of columns (nested
            tags flattened into 'parent.child' notation)
      columns: list[str] — column order by first appearance
      own_account: str | None — statement owner account if recognized
                    (specific to the iBank format), otherwise None
    Raises XMLParseError if the file is not valid or the structure cannot be recognized.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise XMLParseError(f"XML is not valid: {e}")

    own_account = find_own_account(root)

    elements_with_depth = list(_walk_with_depth(root))
    all_elements = [el for el, _ in elements_with_depth]

    freq = Counter(_local_tag(el.tag) for el in all_elements)

    best_tag = None
    best_count = 0

    # Pass 1: a tag that repeats more than once in the whole document and carries columns
    #    (the most reliable signal - the classic case of several transactions in a file)
    for el in all_elements:
        tag = _local_tag(el.tag)
        if freq[tag] > 1 and len(list(el)) > 0:
            if freq[tag] > best_count:
                best_count = freq[tag]
                best_tag = tag

    # Pass 2 (fallback): the file may contain only ONE transaction (e.g. a small
    #    statement for one specific case/order), so no tag has freq>1.
    #    In that case we look for a "homogeneous container" — an element whose
    #    children are ALL the same tag (even if there is only one child), which is a
    #    sign that this tag is by nature a list row. We pick the deepest/most specific
    #    such tag (e.g. stmttrn inside banktranlist, not stmtrs inside stmtrslist).
    if best_tag is None:
        candidates = []
        for parent, depth in elements_with_depth:
            children = list(parent)
            if not children:
                continue
            child_tags = [_local_tag(c.tag) for c in children]
            if len(set(child_tags)) == 1 and len(list(children[0])) > 0:
                candidates.append((child_tags[0], len(children), depth + 1))
        if candidates:
            candidates.sort(key=lambda c: (-c[2], -c[1]))
            best_tag = candidates[0][0]

    if best_tag is None:
        raise XMLParseError("Could not recognize any repeating rows in the XML.")

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
        raise XMLParseError(f"Rows were found ('{best_tag}') but they have no columns.")

    return rows, columns, own_account
import hashlib
import re
from dataclasses import dataclass, asdict
from typing import Optional


ITEM_RE = re.compile(
    r"(?<!\d)(?P<code>\d{8,14})(?!\d)\s+"
    r"(?P<description>(?:(?!\b\d{8,14}\b).)+?)\s+\$?"
    r"(?P<price>\d+\.\d{2})\s*(?P<tax>[A-Z]?)"
    r"(?=\s+(?:\d{8,14}\b|TOTAL\b|SUBTOTAL\b|NUMBER\b)|\s*$)"
)
ITEM_COUNT_RE = re.compile(r"NUMBER\s+OF\s+ITEMS\s*:?\s*(\d+)", re.IGNORECASE)


WEIGHT_RE = re.compile(
    r"(?P<weight>\d+(?:\.\d+)?)\s*lb\s*@\s*(?:1\s*lb\s*/\s*)?(?P<unit_price>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

DATE_RE = re.compile(r"\b(?P<date>\d{1,2}/\d{1,2}/\d{2,4})\b")
TIME_RE = re.compile(r"\b(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\b")
STORE_RE = re.compile(r"\b(?:STORE|MEIJER|ST)\s*[#:]?\s*(?P<store>\d{1,5})\b", re.IGNORECASE)
TERMINAL_RE = re.compile(r"\b(?:TERMINAL|TM)\s*[#:]?\s*(?P<terminal>\d+)\b", re.IGNORECASE)
TRANS_RE = re.compile(r"\b(?:TRANSACTION|TRANS|TX)\s*[#:]?\s*(?P<trans>\d+)\b", re.IGNORECASE)
OPERATOR_RE = re.compile(r"\b(?:OPERATOR|OP)\s*[#:]?\s*(?P<operator>\d+)\b", re.IGNORECASE)
TOTAL_RE = re.compile(r"\bTOTAL\s+\$?\s*(?P<total>\d+\.\d{2})\b", re.IGNORECASE)


@dataclass
class ParsedItem:
    raw_code: str
    normalized_code: str
    receipt_description: str
    quantity: float = 1.0
    weight: Optional[float] = None
    weight_unit: Optional[str] = None
    unit_price: Optional[float] = None
    line_total: Optional[float] = None
    tax_flag: Optional[str] = None
    parse_confidence: float = 0.95


def normalize_code(code: str) -> str:
    digits = re.sub(r"\D", "", code or "")
    if len(digits) == 11:
        return "0" + digits
    return digits


def _search(pattern, text, key):
    m = pattern.search(text)
    return m.group(key) if m else None


def parse_meijer_receipt(text: str) -> dict:
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    items: list[ParsedItem] = []
    last_item = None

    # Some browser/PDF layouts split a product into several text lines.
    rows = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.match(r"^\d{8,14}(?!\d)(?:\s|$)", line) and not ITEM_RE.search(line):
            while index + 1 < len(lines) and not ITEM_RE.search(line):
                next_line = lines[index + 1]
                if re.match(r"^(?:\d{8,14}(?!\d)(?:\s|$)|TOTAL\b|SUBTOTAL\b|NUMBER\b)", next_line):
                    break
                index += 1
                line += " " + next_line
        rows.append(line)
        index += 1

    for line in rows:
        wm = WEIGHT_RE.search(line)
        if wm and last_item:
            last_item.weight = float(wm.group("weight"))
            last_item.weight_unit = "lb"
            last_item.quantity = last_item.weight
            last_item.unit_price = float(wm.group("unit_price"))
            last_item.parse_confidence = min(last_item.parse_confidence, 0.90)
            continue

        # A flattened browser line may contain several distinct receipt items.
        for m in ITEM_RE.finditer(line):
            item = ParsedItem(
                raw_code=m.group("code"),
                normalized_code=normalize_code(m.group("code")),
                receipt_description=m.group("description").strip(),
                line_total=float(m.group("price")),
                tax_flag=(m.group("tax") or None),
            )
            items.append(item)
            last_item = item

    purchase_date = _search(DATE_RE, text, "date")
    purchase_time = _search(TIME_RE, text, "time")
    store_number = _search(STORE_RE, text, "store")
    terminal = _search(TERMINAL_RE, text, "terminal")
    transaction_number = _search(TRANS_RE, text, "trans")
    operator = _search(OPERATOR_RE, text, "operator")

    total_match = TOTAL_RE.search(text)
    total = float(total_match.group("total")) if total_match else None

    fingerprint_base = "|".join(
        [
            purchase_date or "",
            purchase_time or "",
            store_number or "",
            terminal or "",
            transaction_number or "",
            operator or "",
            str(total or ""),
            str(len(items)),
        ]
    )
    if fingerprint_base.strip("|") == str(len(items)):
        fingerprint_base += "|" + hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()

    fingerprint = hashlib.sha256(fingerprint_base.encode("utf-8")).hexdigest()

    item_count_match = ITEM_COUNT_RE.search(text)

    return {
        "expected_item_count": int(item_count_match.group(1)) if item_count_match else None,
        "purchase_date": purchase_date,
        "purchase_time": purchase_time,
        "store_number": store_number,
        "terminal": terminal,
        "transaction_number": transaction_number,
        "operator": operator,
        "total": total,
        "fingerprint": fingerprint,
        "items": [asdict(x) for x in items],
    }

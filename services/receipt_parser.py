"""Choose a receipt parser from the store name in its header."""
import re
from services.meijer_parser import parse_meijer_receipt
from services.costco_parser import parse_costco_receipt, STORE_RE, ITEM_RE


def parse_receipt(text):
    # Text comes from the receipt DOM, PDF extraction, or logo/header OCR.
    match = re.search(r'\b(C[O0]ST\s*C[O0]|MEIJER)\b', text[:4000], re.I)
    if match and re.sub(r'\s', '', match.group(1)).lower().replace('0', 'o') == 'costco':
        return parse_costco_receipt(text)
    # Selectable PDFs can omit the bitmap logo. Require both a warehouse
    # header and Costco's short-code item layout before falling back.
    if not match and STORE_RE.search(text[:2000]) and ITEM_RE.search(text):
        return parse_costco_receipt(text)
    # Keep support for historical pasted Meijer receipts without a logo.
    return {'store': 'Meijer', **parse_meijer_receipt(text)}

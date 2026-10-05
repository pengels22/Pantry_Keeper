"""Parse Costco warehouse receipts; item numbers are retailer codes, not UPCs."""
import hashlib
import re
from dataclasses import asdict
from services.meijer_parser import ParsedItem, DATE_RE, TIME_RE, _search

ITEM_RE = re.compile(
    r'(?:^|\s)(?:E\s+)?(?P<code>\d{3,10})\s+'
    r'(?P<description>[^\n]+?)\s+\$?(?P<price>\d+\.\d{2})\s*(?P<tax>[NY])?'
    r'(?=\s+(?:E\s+)?\d{3,10}\s|\s+(?:SUBTOTAL|TOTAL|TAX)\b|\s*$)', re.I)
DISCOUNT_RE = re.compile(r'^\s*(?:E\s+)?(?P<code>\d{3,10})\s+/\s*(?P<target>\d{3,10})\s+\$?(?P<amount>\d+\.\d{2})\s*-\s*[NY]?\s*$', re.I)
STOP_RE = re.compile(r'^(?:SUB\s*TOTAL|TOTAL|TAX|INSTANT SAVINGS|ITEMS SOLD|TOTAL NUMBER|NUMBER OF ITEMS|CHANGE|CASH|VISA|MASTERCARD|DEBIT|CREDIT|TENDER)\b', re.I)
TOTAL_RE = re.compile(r'^\s*(?:\*+\s*)?TOTAL\s*:?\s*\$?\s*(\d+\.\d{2})', re.I | re.M)
STORE_RE = re.compile(r'^\s*(?:[A-Z][A-Z .-]*\s+)?#\s*(\d{1,5})\s*$', re.I | re.M)
COUNT_RE = re.compile(r'(?:TOTAL\s+NUMBER\s+OF\s+ITEMS\s+SOLD|NUMBER\s+OF\s+ITEMS|ITEMS\s+SOLD)\s*:?\s*(\d+)', re.I)


def parse_costco_receipt(text):
    lines = [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines() if line.strip()]
    items, discounts, warnings = [], [], []
    index = 0
    while index < len(lines):
        line = lines[index]
        # Browser DOM and PDFs may put the E marker, item number, name, and
        # price on separate lines. Stop at the next item or receipt footer.
        if line == 'E' and index + 1 < len(lines):
            index += 1
            line += ' ' + lines[index]
        if re.match(r'^(?:E\s+)?\d{3,10}(?:\s|$)', line) and not ITEM_RE.search(line) and not DISCOUNT_RE.match(line):
            for _ in range(4):
                if index + 1 >= len(lines):
                    break
                following = lines[index + 1]
                if STOP_RE.match(following) or re.match(r'^(?:E\s+)?\d{3,10}(?:\s|$)', following):
                    break
                index += 1
                line += ' ' + following
                if ITEM_RE.search(line) or DISCOUNT_RE.match(line):
                    break
        index += 1
        discount = DISCOUNT_RE.match(line)
        if discount:
            amount = float(discount['amount'])
            target = next((item for item in reversed(items) if item.raw_code == discount['target']), None)
            discounts.append({'raw_code': discount['code'], 'item_number': discount['target'], 'amount': amount})
            if target and target.line_total >= amount:
                target.line_total = round(target.line_total - amount, 2)
                target.unit_price = target.line_total
            else:
                warnings.append(f"Discount for item {discount['target']} was not matched; check receipt prices.")
            continue
        if STOP_RE.match(line):
            continue
        for match in ITEM_RE.finditer(line):
            description = match['description'].strip()
            # Slash-prefixed rows are coupons, never inventory purchases.
            if description.startswith('/') or re.search(r'\b(?:MEMBER|APPROVED|AUTH|ACCOUNT|BALANCE)\b', description, re.I):
                continue
            price = float(match['price'])
            items.append(ParsedItem(raw_code=match['code'], normalized_code='costco:' + match['code'],
                receipt_description=description, line_total=price, unit_price=price,
                tax_flag=(match['tax'] or '').upper() or None))
    def metadata(label):
        match = re.search(r'\b(?:' + label + r')\s*[#:]?\s*(\d+)', text, re.I)
        return match.group(1) if match else None
    date = _search(DATE_RE, text, 'date')
    time = _search(TIME_RE, text, 'time')
    store_match = STORE_RE.search(text)
    store = store_match.group(1) if store_match else metadata('WAREHOUSE|STORE|WHSE')
    terminal, transaction, operator = metadata('TRM|TERMINAL'), metadata('TRN|TRANSACTION|TRANS'), metadata('OP|OPERATOR')
    total_match, count_match = TOTAL_RE.search(text), COUNT_RE.search(text)
    total = float(total_match.group(1)) if total_match else None
    identity = '|'.join([date or '', time or '', store or '', terminal or '', transaction or '', operator or '', str(total)])
    # With no transaction identifiers, include purchase contents to prevent
    # unrelated warehouse receipts with the same total from colliding.
    if not transaction:
        identity += '|' + repr([(item.raw_code, item.line_total) for item in items])
    fingerprint = hashlib.sha256(('Costco|' + identity).encode()).hexdigest()
    return {'store': 'Costco', 'purchase_date': date, 'purchase_time': time, 'store_number': store,
        'terminal': terminal, 'transaction_number': transaction, 'operator': operator, 'total': total,
        'fingerprint': fingerprint, 'expected_item_count': int(count_match.group(1)) if count_match else None,
        'items': [asdict(item) for item in items], 'discounts': discounts, 'warnings': warnings}

"""One representation for receipt, manual, and barcode UPC input."""
import re


def normalize_upc(value: str | None) -> str:
    """Keep ASCII digits, including every leading zero; never pad or cast to int."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("UPC must be text so leading zeros are preserved.")
    return re.sub(r"[^0-9]", "", value)


def normalize_catalog_code(value: str | None) -> str:
    """Preserve scoped retailer item numbers without treating them as barcodes."""
    if isinstance(value, str) and value.lower().startswith('costco:'):
        code = value.split(':', 1)[1].strip()
        if not re.fullmatch(r'[0-9]{3,10}', code):
            raise ValueError('Costco item number must contain 3–10 digits.')
        return 'costco:' + code
    return normalize_upc(value)

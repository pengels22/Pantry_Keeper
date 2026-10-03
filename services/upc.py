"""One representation for receipt, manual, and barcode UPC input."""
import re


def normalize_upc(value: str | None) -> str:
    """Keep ASCII digits, including every leading zero; never pad or cast to int."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("UPC must be text so leading zeros are preserved.")
    return re.sub(r"[^0-9]", "", value)

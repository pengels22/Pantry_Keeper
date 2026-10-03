"""Extract PDF receipt text, using OCR for scanned pages."""
import math
import pymupdf

from services.meijer_parser import parse_meijer_receipt
from services.ocr import extract_text_from_image


class InvalidReceiptPDF(ValueError):
    pass


class ReceiptPDFTooLarge(ValueError):
    pass


def receipt_word_rows(page):
    """Restore rows when PDF text blocks separate code, description, and price."""
    words = sorted(page.get_text('words'), key=lambda word: ((word[1] + word[3]) / 2, word[0]))
    rows = []
    for word in words:
        center = (word[1] + word[3]) / 2
        height = word[3] - word[1]
        if rows and abs(rows[-1][0] - center) <= min(rows[-1][1], height) * 0.45:
            rows[-1][2].append(word)
        else:
            rows.append([center, height, [word]])
    return '\n'.join(' '.join(word[4] for word in sorted(row[2], key=lambda word: word[0])) for row in rows)


def extract_receipt_pdf(pdf_bytes: bytes) -> tuple[str, bool]:
    if not pdf_bytes.startswith(b'%PDF-'):
        raise InvalidReceiptPDF('This file is not a PDF receipt.')
    try:
        document = pymupdf.open(stream=pdf_bytes, filetype='pdf')
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise InvalidReceiptPDF('Could not read this PDF receipt.') from exc
    with document:
        if document.needs_pass:
            raise InvalidReceiptPDF('Password-protected receipts are not supported.')
        if document.page_count > 10:
            raise ReceiptPDFTooLarge('Open an individual receipt with at most 10 pages.')
        pages = []
        used_ocr = False
        for page in document:
            candidates = [page.get_text('text', sort=True), page.get_text('text'), receipt_word_rows(page)]
            text = max(candidates, key=lambda value: len(parse_meijer_receipt(value)['items']))
            parsed = parse_meijer_receipt(text)
            expected = parsed['expected_item_count']
            if not parsed['items'] or (expected is not None and len(parsed['items']) < expected):
                area = page.rect.width * page.rect.height
                if not math.isfinite(area) or area <= 0:
                    raise InvalidReceiptPDF('The PDF contains an invalid page size.')
                scale = min(2.5, math.sqrt(12_000_000 / area))
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                ocr_text = extract_text_from_image(pixmap.tobytes('png'))
                if len(parse_meijer_receipt(ocr_text)['items']) > len(parsed['items']):
                    text = ocr_text
                    used_ocr = True
            pages.append(text)
        return '\n'.join(pages), used_ocr

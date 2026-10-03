import io
import os
from PIL import Image

try:
    import pytesseract
except Exception:
    pytesseract = None


class OCRUnavailable(RuntimeError):
    pass


def extract_text_from_image(image_bytes: bytes) -> str:
    if os.getenv("ENABLE_OCR", "true").lower() not in {"1", "true", "yes", "on"}:
        raise OCRUnavailable("OCR is disabled by configuration.")

    if pytesseract is None:
        raise OCRUnavailable("pytesseract is not installed.")

    cmd = os.getenv("TESSERACT_CMD", "").strip()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    try:
        text = pytesseract.image_to_string(image, config="--psm 6", timeout=45)
    except pytesseract.TesseractNotFoundError:
        raise OCRUnavailable("Tesseract is not installed or its configured path is incorrect.")
    except RuntimeError:
        raise OCRUnavailable("Receipt OCR timed out. Try a smaller, clearer image.")
    return text

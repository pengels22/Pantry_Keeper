"""Costco parsing and store-scoped product identity through the receipt workflow."""
import io
import unittest
from unittest.mock import AsyncMock, patch
import test_workflow
import pymupdf
from PIL import Image, ImageDraw, ImageFont
from fastapi.testclient import TestClient
from app import app
from db import Base, engine, SessionLocal
from models import Receipt
from services.receipt_parser import parse_receipt
from services.schema import initialize_database

RECEIPT = '''COSTCO WHOLESALE
WYOMING #744
4901 WILSON AVE SW
WYOMING, MI 49418
Member 112000403261
E 1453434 CHEESE BREAD 10.39 N
E 1731084 TROPICANA OJ 10.79 N
E 5331 ORG CLASSICO 12.79 N
E 33724 GROUND BEEF 36.12 N
E 1840900 DIXIEDEEPDSH 14.99 Y
388210 /1840900 3.00-
E 5331 ORG CLASSICO 12.79 N
SUBTOTAL 94.87
TAX 0.72
TOTAL 95.59
TOTAL NUMBER OF ITEMS SOLD: 6
09/09/2026 10:09
OP# 9
TRM# 4
TRN# 1009
'''


class CostcoTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        self.client = TestClient(app)

    def test_header_metadata_prices_and_discounts(self):
        parsed = parse_receipt(RECEIPT)
        self.assertEqual(parsed['store'], 'Costco')
        self.assertEqual(parsed['store_number'], '744')
        self.assertEqual(parsed['purchase_date'], '09/09/2026')
        self.assertEqual(parsed['purchase_time'], '10:09')
        self.assertEqual(parsed['terminal'], '4')
        self.assertEqual(parsed['transaction_number'], '1009')
        self.assertEqual(parsed['total'], 95.59)
        self.assertEqual(parsed['expected_item_count'], 6)
        self.assertEqual(len(parsed['items']), 6)
        self.assertEqual(parsed['items'][0]['raw_code'], '1453434')
        self.assertEqual(parsed['items'][0]['normalized_code'], 'costco:1453434')
        self.assertEqual(parsed['items'][4]['line_total'], 11.99)
        self.assertEqual(parsed['items'][4]['tax_flag'], 'Y')
        self.assertEqual(len(parsed['discounts']), 1)
        self.assertFalse(parsed['warnings'])
        self.assertEqual(parsed['items'][3]['quantity'], 1)  # Weight is not present in this receipt.

    def test_split_rows_missing_bitmap_logo_and_ocr_logo(self):
        text = 'WYOMING #744\nE\n1453434\nCHEESE BREAD\n10.39 N\nTOTAL 10.39'
        parsed = parse_receipt(text)
        self.assertEqual(parsed['store'], 'Costco')
        self.assertEqual(len(parsed['items']), 1)
        self.assertEqual(parse_receipt(RECEIPT.replace('COSTCO', 'C0STC0'))['store'], 'Costco')
        self.assertEqual(parse_receipt('MEIJER\n012345678901 MILK 3.99')['store'], 'Meijer')
        self.assertEqual(parse_receipt('012345678901 MILK 3.99')['store'], 'Meijer')

    def test_unmatched_discount_is_review_warning_not_inventory(self):
        parsed = parse_receipt('COSTCO\nE 3882772 SBUX HOLIDAY 44.99 N\n387964 /3123235 9.00-\nTOTAL 35.99')
        self.assertEqual(len(parsed['items']), 1)
        self.assertEqual(parsed['items'][0]['line_total'], 44.99)
        self.assertTrue(parsed['warnings'])

    def test_browser_review_import_local_reuse_and_scope_isolation(self):
        # Same digits in Meijer's catalog must not identify a Costco product.
        self.client.post('/api/products', json={'upc': '5331', 'name': 'Unrelated Meijer product'})
        scan = self.client.post('/api/receipts/browser', json={'text': RECEIPT},
            headers={'Authorization': 'Bearer test-token'})
        self.assertEqual(scan.status_code, 200)
        data = scan.json()
        self.assertEqual(data['parsed']['store'], 'Costco')
        self.assertEqual(data['lookup_items'], [])
        self.assertEqual(len(data['unknown_items']), 6)
        with patch('app.lookup_open_food_facts', new_callable=AsyncMock) as external:
            preview = self.client.post('/api/receipts/resolve-preview', json=data)
            external.assert_not_awaited()
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(all(row['status'] == 'unresolved' for row in preview.json()['items']))
        self.assertIn('costco.com', preview.json()['items'][0]['costco_search_url'])
        data['selected_products'] = {'costco:5331': {'name': 'Organic Classico'}}
        response = self.client.post('/api/receipts/import', json=data)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['resolved_items'], 2)
        products = self.client.get('/api/dashboard').json()['products']
        product = next(p for p in products if p['upc'] == 'costco:5331')
        self.assertEqual(product['inventory_quantity'], 2)
        self.assertIsNone(product['gtin_normalized'])
        self.assertEqual(self.client.post('/api/receipts/import', json=data).status_code, 409)
        with SessionLocal() as db:
            self.assertEqual(db.query(Receipt).one().store, 'Costco')
        # Restart migration preserves scoped identities and does not collide.
        initialize_database(engine)
        rescan = self.client.post('/api/receipts/browser', json={'text': RECEIPT},
            headers={'Authorization': 'Bearer test-token'}).json()
        self.assertEqual(len(rescan['known_items']), 2)
        unknown = self.client.get('/api/unknown-products').json()[0]
        self.assertTrue(unknown['upc'].startswith('costco:'))
        self.assertIsNone(unknown['meijer_search_url'])
        saved = self.client.post(f"/api/unknown-products/{unknown['receipt_item_id']}/resolve", json={'name': 'Costco product'})
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()['product']['upc'].startswith('costco:'))

    def test_image_ocr_and_pdf_store_detection(self):
        image = Image.new('RGB', (1200, 500), 'white')
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 32)
        ImageDraw.Draw(image).multiline_text((20,20), 'COSTCO WHOLESALE\nWYOMING #744\nE 1453434 CHEESE BREAD 10.39 N\nTOTAL 10.39', font=font, fill='black', spacing=12)
        buffer = io.BytesIO(); image.save(buffer, format='PNG')
        scan = self.client.post('/api/receipts/scan-image', files={'file': ('costco.png', buffer.getvalue(), 'image/png')})
        self.assertEqual(scan.status_code, 200)
        self.assertEqual(scan.json()['parsed']['store'], 'Costco')
        self.assertEqual(len(scan.json()['parsed']['items']), 1)
        document = pymupdf.open(); page = document.new_page()
        page.insert_text((40,40), RECEIPT)
        pdf = document.tobytes(); document.close()
        with patch('services.pdf.extract_text_from_image') as ocr:
            response = self.client.post('/api/receipts/browser-pdf', files={'file': ('costco.pdf', pdf, 'application/pdf')}, headers={'Authorization': 'Bearer test-token'})
            ocr.assert_not_called()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['parsed']['store'], 'Costco')
        self.assertEqual(len(response.json()['parsed']['items']), 6)

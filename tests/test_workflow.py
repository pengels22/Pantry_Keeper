import os
import io
import pymupdf
from unittest.mock import patch, AsyncMock
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TEMP = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(TEMP.name) / 'test.db')
os.environ['RECEIPT_API_TOKEN'] = 'test-token'
from PIL import Image, ImageDraw, ImageFont
from fastapi.testclient import TestClient
from app import app
from models import BrowserDraft, utc_now
from db import SessionLocal
from datetime import timedelta
from services.meijer_parser import parse_meijer_receipt
from services.pdf import extract_receipt_pdf
from db import Base, engine


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        self.client = TestClient(app)

    def test_receipt_resolution_inventory_and_duplicates(self):
        scan = self.client.post('/api/receipts/scan-text', json={
            'text': 'STORE 12\n10/03/2026 12:30\nTRANSACTION 123\n012345678901 MILK 3.99\nTOTAL 3.99'
        }).json()
        self.assertEqual(len(scan['parsed']['items']), 1)
        imported = self.client.post('/api/receipts/import', json=scan)
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(self.client.post('/api/receipts/import', json=scan).status_code, 409)
        unknown = self.client.get('/api/unknown-products').json()[0]
        url = f"/api/unknown-products/{unknown['receipt_item_id']}/resolve"
        product = self.client.post(url, json={'name': 'Milk'}).json()['product']
        self.assertEqual(self.client.post(url, json={'name': 'Milk'}).status_code, 200)
        self.assertEqual(self.client.get('/api/dashboard').json()['products'][0]['inventory_quantity'], 1)
        inventory_url = f"/api/inventory/{product['id']}"
        self.assertEqual(self.client.post(inventory_url, json={'quantity': 0.5}).status_code, 200)
        self.assertEqual(self.client.get('/api/dashboard').json()['products'][0]['inventory_quantity'], 0.5)
        for quantity in [-1, 'bad', 'nan', 'inf']:
            self.assertEqual(self.client.post(inventory_url, json={'quantity': quantity}).status_code, 400)
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_image_ocr(self):
        image = Image.new('RGB', (1100, 180), 'white')
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 36)
        ImageDraw.Draw(image).text((20, 30), '012345678901 MILK 3.99', font=font, fill='black')
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        response = self.client.post('/api/receipts/scan-image',
                                    files={'file': ('receipt.png', buffer.getvalue(), 'image/png')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['parsed']['items'][0]['raw_code'], '012345678901')
        upload = {'file': ('receipt.png', buffer.getvalue(), 'image/png')}
        self.assertEqual(self.client.post('/api/receipts/browser-image', files=upload).status_code, 401)
        response = self.client.post('/api/receipts/browser-image', files=upload,
                                    headers={'Authorization': 'Bearer test-token'})
        self.assertEqual(response.status_code, 200)
        draft = self.client.get('/api/receipts/browser-drafts/' + response.json()['draft_id']).json()
        self.assertEqual(draft['source_format'], 'image')
        self.assertEqual(draft['parsed']['items'][0]['raw_code'], '012345678901')
        self.assertTrue(draft['ocr_text'])
        self.assertEqual(self.client.post('/api/receipts/browser-image',
            files={'file': ('bad.png', b'not an image', 'image/png')},
            headers={'Authorization': 'Bearer test-token'}).status_code, 400)


    def test_browser_token_and_text(self):
        payload = {'text': '012345678901 MILK 3.99'}
        self.assertEqual(self.client.post('/api/receipts/browser', json=payload).status_code, 401)
        response = self.client.post('/api/receipts/browser', json=payload,
                                    headers={'Authorization': 'Bearer test-token'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['parsed']['items']), 1)
        draft_id = response.json()['draft_id']
        draft_url = f'/api/receipts/browser-drafts/{draft_id}'
        scan = self.client.get(draft_url)
        self.assertEqual(scan.status_code, 200)
        self.assertEqual(scan.json()['raw_text'], payload['text'])
        self.assertEqual(scan.json()['source_format'], 'webpage_text')
        self.assertEqual(self.client.post('/api/receipts/import', json=scan.json()).status_code, 200)
        self.assertEqual(self.client.get(draft_url).status_code, 200)
        with SessionLocal() as db:
            db.get(BrowserDraft, draft_id).expires_at = utc_now() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.client.get(draft_url).status_code, 410)
        self.assertEqual(self.client.get(draft_url).status_code, 404)

    def test_receipt_footer_and_weight_format(self):
        parsed = parse_meijer_receipt(
            '10/03/26\n88567700204 ONION LB 1.07 F\n0.72 lb @ 1 lb / 1.49\n'
            'TOTAL 22.30\nTx:25 Op:565 Tm:114 St:199 10:49:58')
        self.assertEqual(parsed['store_number'], '199')
        self.assertEqual(parsed['terminal'], '114')
        self.assertEqual(parsed['transaction_number'], '25')
        self.assertEqual(parsed['operator'], '565')
        self.assertEqual(parsed['items'][0]['quantity'], 0.72)
        self.assertEqual(parsed['items'][0]['unit_price'], 1.49)

    def test_browser_pdf_text_and_invalid_files(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((40, 40), '012345678901 MILK 3.99\nTOTAL 3.99', fontname='cour')
            pdf = doc.tobytes()
        headers = {'Authorization': 'Bearer test-token'}
        files = {'file': ('receipt.pdf', pdf, 'application/pdf')}
        self.assertEqual(self.client.post('/api/receipts/browser-pdf', files=files).status_code, 401)
        with patch('services.pdf.extract_text_from_image', side_effect=AssertionError('Text PDF should not need OCR')):
            response = self.client.post('/api/receipts/browser-pdf', files=files, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['parsed']['items']), 1)
        draft = self.client.get('/api/receipts/browser-drafts/' + response.json()['draft_id']).json()
        self.assertEqual(draft['source_format'], 'pdf')
        self.assertIsNone(draft['ocr_text'])
        for data in [b'not a pdf', b'%PDF-broken']:
            self.assertEqual(self.client.post('/api/receipts/browser-pdf',
                files={'file': ('bad.pdf', data, 'application/pdf')}, headers=headers).status_code, 400)
        with pymupdf.open() as doc:
            for _ in range(11):
                doc.new_page()
            response = self.client.post('/api/receipts/browser-pdf',
                files={'file': ('big.pdf', doc.tobytes(), 'application/pdf')}, headers=headers)
        self.assertEqual(response.status_code, 413)

    def test_browser_scanned_pdf_ocr(self):
        image = Image.new('RGB', (1100, 180), 'white')
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 36)
        ImageDraw.Draw(image).text((20, 30), '012345678901 MILK 3.99', font=font, fill='black')
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        with pymupdf.open() as doc:
            page = doc.new_page(width=550, height=90)
            page.insert_image(page.rect, stream=buffer.getvalue())
            pdf = doc.tobytes()
        response = self.client.post('/api/receipts/browser-pdf',
            files={'file': ('scan.pdf', pdf, 'application/pdf')},
            headers={'Authorization': 'Bearer test-token'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['parsed']['items'][0]['raw_code'], '012345678901')
        self.assertTrue(response.json()['ocr_text'])

    def test_multiple_items_on_one_line_and_wrapped_lines(self):
        flattened = '012345678901 MILK 3.99 F 012345678902 BREAD 2.49 F 012345678903 EGGS 4.99 F'
        parsed = parse_meijer_receipt(flattened + '\nNUMBER OF ITEMS 3')
        self.assertEqual([item['receipt_description'] for item in parsed['items']], ['MILK', 'BREAD', 'EGGS'])
        self.assertEqual(parsed['expected_item_count'], 3)
        wrapped = '012345678901\nMILK\n3.99\nF\n012345678902\nBREAD\n2.49\nF'
        self.assertEqual(len(parse_meijer_receipt(wrapped)['items']), 2)

    def test_import_saves_all_lines_including_unknown_products(self):
        text = '012345678901 MILK 3.99 F 012345678902 BREAD 2.49 F 012345678903 EGGS 4.99 F'
        self.client.post('/api/products', json={'receipt_code_raw': '012345678901', 'name': 'Milk'})
        scan = self.client.post('/api/receipts/scan-text', json={'text': text}).json()
        result = self.client.post('/api/receipts/import', json=scan)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['imported_items'], 3)
        self.assertEqual(result.json()['resolved_items'], 1)
        self.assertEqual(result.json()['unresolved_items'], 2)
        self.assertEqual(len(self.client.get('/api/unknown-products').json()), 2)

    def test_partial_pdf_text_tries_ocr_when_item_count_is_short(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((40, 40), '012345678901 MILK 3.99\nNUMBER OF ITEMS 3', fontname='cour')
            pdf = doc.tobytes()
        full_text = '012345678901 MILK 3.99 F\n012345678902 BREAD 2.49 F\n012345678903 EGGS 4.99 F\nNUMBER OF ITEMS 3'
        with patch('services.pdf.extract_text_from_image', return_value=full_text):
            text, used_ocr = extract_receipt_pdf(pdf)
        self.assertTrue(used_ocr)
        self.assertEqual(len(parse_meijer_receipt(text)['items']), 3)

    def test_meijer_suggestions_flow_into_catalog_and_inventory(self):
        headers = {'Authorization': 'Bearer test-token'}
        scan = self.client.post('/api/receipts/browser', json={'text': '71928395643 FRZN VEGETABLE 1.09 F'}, headers=headers).json()
        code = '71928395643'
        product = {'name': 'Meijer Steamable Mixed Vegetables, 12 oz', 'brand': 'Meijer', 'size': '12 oz',
                   'url': 'https://www.meijer.com/shopping/product/mixed-vegetables/71928395643.html'}
        attach = f"/api/receipts/browser-drafts/{scan['draft_id']}/meijer-products"
        payload = {'products': {code: [product, {**product, 'url': 'https://attacker.test/shopping/product/71928395643.html'}]}, 'errors': []}
        self.assertEqual(self.client.post(attach, json=payload).status_code, 401)
        self.assertEqual(self.client.post(attach, json=payload, headers=headers).status_code, 200)
        draft = self.client.get('/api/receipts/browser-drafts/' + scan['draft_id']).json()
        self.assertEqual(len(draft['meijer_products'][code]), 1)
        with patch('app.lookup_open_food_facts', new_callable=AsyncMock) as fallback:
            preview = self.client.post('/api/receipts/resolve-preview', json={
                'parsed': draft['parsed'], 'meijer_products': draft['meijer_products']}).json()
            fallback.assert_not_called()
        suggestion = preview['items'][0]['suggestion']
        self.assertEqual(suggestion['match_kind'], 'exact_code')
        self.assertEqual(suggestion['brand'], 'Meijer')
        self.assertEqual(suggestion['size'], '12 oz')
        self.client.post('/api/products', json={**suggestion, 'receipt_code_raw': code})
        imported = self.client.post('/api/receipts/import', json=draft).json()
        self.assertEqual(imported['resolved_items'], 1)
        dashboard = self.client.get('/api/dashboard').json()
        self.assertEqual(dashboard['products'][0]['inventory_quantity'], 1)
        self.assertEqual(dashboard['products'][0]['lookup_source'], 'meijer')
        again = self.client.post('/api/receipts/browser', json={'text':'71928395643 FRZN VEGETABLE 1.09 F'}, headers=headers).json()
        self.assertEqual(again['lookup_items'], [])
        self.assertEqual(self.client.post(attach, json={'products': {'99999999999': [product]}}, headers=headers).status_code, 400)

    def test_browser_invalid_receipts(self):
        headers = {'Authorization': 'Bearer test-token'}
        for text, expected in [(None, 400), ('', 400), ('Not a receipt', 422), ('x' * 500001, 413)]:
            response = self.client.post('/api/receipts/browser', json={'text': text}, headers=headers)
            self.assertEqual(response.status_code, expected)



if __name__ == '__main__':
    unittest.main()

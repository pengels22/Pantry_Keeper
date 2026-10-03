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
from models import BrowserDraft, Product, ProductLookupCache, ProductLookupRateLimit, utc_now
from db import SessionLocal
from datetime import timedelta
from services.meijer_parser import parse_meijer_receipt
from services.pdf import extract_receipt_pdf
from sqlalchemy import event, create_engine, inspect, text
import httpx
from services.schema import initialize_database
from services.upc import normalize_upc
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
        draft['selected_products'] = {code: suggestion}
        imported = self.client.post('/api/receipts/import', json=draft).json()
        self.assertEqual(imported['resolved_items'], 1)
        dashboard = self.client.get('/api/dashboard').json()
        self.assertEqual(dashboard['products'][0]['inventory_quantity'], 1)
        self.assertEqual(dashboard['products'][0]['lookup_source'], 'meijer')
        again = self.client.post('/api/receipts/browser', json={'text':'71928395643 FRZN VEGETABLE 1.09 F'}, headers=headers).json()
        self.assertEqual(again['lookup_items'], [])
        self.assertEqual(self.client.post(attach, json={'products': {'99999999999': [product]}}, headers=headers).status_code, 400)

    def test_import_saves_search_suggestion_and_rejects_invalid_selection(self):
        scan = self.client.post('/api/receipts/scan-text', json={
            'text': '71928395643 FRZN VEGETABLE 1.09 F'}).json()
        product = {'name': 'Mixed Vegetables', 'brand': 'Meijer', 'size': '12 oz',
                   'lookup_source': 'meijer', 'match_kind': 'search_result'}
        scan['selected_products'] = {'71928395643': dict(product), 'other': dict(product)}
        self.assertEqual(self.client.post('/api/receipts/import', json=scan).status_code, 400)
        self.assertEqual(self.client.get('/api/dashboard').json()['products'], [])
        scan['selected_products'].pop('other')
        result = self.client.post('/api/receipts/import', json=scan)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['resolved_items'], 1)
        self.assertEqual(self.client.get('/api/unknown-products').json(), [])
        saved = self.client.get('/api/dashboard').json()['products'][0]
        self.assertEqual(saved['name'], product['name'])
        self.assertEqual(saved['brand'], product['brand'])
        self.assertEqual(saved['size'], product['size'])
        self.assertEqual(saved['inventory_quantity'], 1)
        scan['selected_products']['71928395643']['name'] = 'Changed'
        self.assertEqual(self.client.post('/api/receipts/import', json=scan).status_code, 409)
        saved = self.client.get('/api/dashboard').json()['products'][0]
        self.assertEqual(saved['name'], product['name'])
        self.assertEqual(saved['inventory_quantity'], 1)

    def test_upc_normalization_preserves_zeros_and_text(self):
        self.assertEqual(normalize_upc(' 00-719 283a95643 '), '0071928395643')
        self.assertEqual(normalize_upc('71928395643'), '71928395643')
        self.assertEqual(normalize_upc('٠١abc'), '')
        with self.assertRaises(ValueError):
            normalize_upc(71928395643)
        result = self.client.post('/api/products', json={'upc': '00-719 283a95643', 'name': 'Vegetables'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['upc'], '0071928395643')
        self.assertEqual(self.client.post('/api/products', json={'upc': 123, 'name': 'Bad'}).status_code, 400)

    def test_known_receipt_is_one_local_query_with_zero_external_traffic(self):
        for code in ['11111111111', '33333333333', '44444444444']:
            self.client.post('/api/products', json={'upc': code, 'name': 'Local ' + code})
        scan = {'parsed': {'items': [{'raw_code': code, 'normalized_code': 'incorrect'}
            for code in ['11111111111', '33333333333', '44444444444', '11111111111']]}}
        queries = []
        def record(conn, cursor, statement, params, context, many):
            if statement.startswith('SELECT') and 'FROM products' in statement:
                queries.append(statement)
        event.listen(engine, 'before_cursor_execute', record)
        try:
            with patch('app.lookup_open_food_facts', new_callable=AsyncMock) as external:
                preview = self.client.post('/api/receipts/resolve-preview', json=scan)
                external.assert_not_awaited()
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(len(queries), 1)
            for item in preview.json()['items']:
                self.assertEqual(item['source'], 'Pantry Keeper')
                self.assertNotIn('meijer_search_url', item)
                self.assertEqual(item['normalized_code'], item['raw_code'])
        finally:
            event.remove(engine, 'before_cursor_execute', record)

    def test_mixed_receipt_only_looks_up_unknown_once_and_uses_cache(self):
        self.client.post('/api/products', json={'upc':'11111111111', 'name':'Known'})
        payload = {'parsed': {'items': [{'raw_code': code} for code in
            ['11111111111', '22222222222', '22222222222']]}}
        suggestion = {'name':'Suggested Milk', 'brand':'Example', 'lookup_source':'open_food_facts'}
        with patch('app.lookup_open_food_facts', new_callable=AsyncMock, return_value=suggestion) as external:
            preview = self.client.post('/api/receipts/resolve-preview', json=payload).json()
            external.assert_awaited_once_with('22222222222')
            again = self.client.post('/api/receipts/resolve-preview', json=payload).json()
            external.assert_awaited_once()
        self.assertEqual(preview['items'][0]['source'], 'Pantry Keeper')
        self.assertEqual(preview['items'][1]['classification'], 'UNKNOWN_UPC')
        self.assertTrue(again['items'][1]['cached'])
        self.assertEqual(again['items'][1]['suggestion']['name'], 'Suggested Milk')
        with SessionLocal() as db:
            cache = db.query(ProductLookupCache).one()
            self.assertEqual(cache.status, 'FOUND')
            self.assertEqual(cache.product_name, 'Suggested Milk')

    def test_not_found_and_errors_are_cached_but_can_be_identified_manually(self):
        for status, outcome in [('NOT_FOUND', None), ('ERROR', httpx.ReadTimeout('timeout'))]:
            self.setUp()
            with patch('app.lookup_open_food_facts', new_callable=AsyncMock) as external:
                if isinstance(outcome, Exception):
                    external.side_effect = outcome
                else:
                    external.return_value = outcome
                first = self.client.get('/api/products/lookup?upc=0071928395643').json()
                second = self.client.get('/api/products/lookup?upc=0071928395643').json()
                self.assertEqual(first['lookup_status'], status)
                self.assertTrue(second['cached'])
                external.assert_awaited_once()
            saved = self.client.post('/api/products', json={'upc':'0071928395643', 'name':'Manual Product'})
            self.assertEqual(saved.status_code, 200)
            with patch('app.lookup_open_food_facts', new_callable=AsyncMock) as external:
                known = self.client.get('/api/products/lookup?upc=00-71928395643').json()
                external.assert_not_awaited()
                self.assertEqual(known['product']['id'], saved.json()['id'])
                self.assertNotIn('meijer_search_url', known)

    def test_public_lookup_limit_is_shared_and_expired_cache_can_retry(self):
        with patch('app.lookup_open_food_facts', new_callable=AsyncMock, return_value=None) as external:
            self.client.get('/api/products/lookup?upc=11111111111')
            limited = self.client.get('/api/products/lookup?upc=22222222222').json()
            self.assertEqual(limited['lookup_status'], 'RATE_LIMITED')
            external.assert_awaited_once()
            with SessionLocal() as db:
                db.query(ProductLookupRateLimit).update({'next_lookup_at':utc_now() - timedelta(seconds=1)})
                db.query(ProductLookupCache).update({'expires_at':utc_now() - timedelta(seconds=1)})
                db.commit()
            self.client.get('/api/products/lookup?upc=11111111111')
            self.assertEqual(external.await_count, 2)

    def test_browser_only_sends_unique_unknown_upcs_for_automatic_meijer_search(self):
        self.client.post('/api/products', json={'upc':'11111111111', 'name':'Known Product'})
        with patch('app.lookup_open_food_facts', new_callable=AsyncMock) as external:
            scan = self.client.post('/api/receipts/browser', json={
                'text':'11111111111 KNOWN 1.00 F\n22222222222 UNKNOWN 2.00 F\n22222222222 UNKNOWN 2.00 F'},
                headers={'Authorization':'Bearer test-token'}).json()
            external.assert_not_awaited()
        self.assertEqual([item['upc'] for item in scan['lookup_items']], ['22222222222'])
        self.assertIn('text=22222222222', scan['lookup_items'][0]['meijer_search_url'])
        self.assertEqual([item['upc'] for item in scan['known_items']], ['11111111111'])
        self.assertEqual([item['upc'] for item in scan['unknown_items']], ['22222222222', '22222222222'])
        self.assertEqual(len(scan['parsed']['items']), 3)

    def test_disabled_public_lookup_does_not_make_requests(self):
        with patch.dict(os.environ, {'ENABLE_PUBLIC_UPC_LOOKUP':'false'}), patch('app.lookup_open_food_facts', new_callable=AsyncMock) as external:
            result = self.client.get('/api/products/lookup?upc=71928395643').json()
            external.assert_not_awaited()
            self.assertEqual(result['classification'], 'UNKNOWN_UPC')
            self.assertEqual(result['lookup_status'], 'DISABLED')

    def test_save_identifies_all_pending_lines_and_retains_local_information(self):
        for index, code in enumerate(['71928395643', '71928395643']):
            scan = {'parsed': {'fingerprint': 'pending-' + str(index),
                'items':[{'raw_code':code, 'quantity':2}]}}
            self.assertEqual(self.client.post('/api/receipts/import', json=scan).status_code, 200)
        rows = self.client.get('/api/unknown-products').json()
        self.assertEqual(len(rows), 2)
        payload = {'name':'Mixed Frozen Vegetables', 'brand':'Meijer', 'category':'Frozen',
                   'unit':'bag', 'notes':'Steam for dinner'}
        url = '/api/unknown-products/' + str(rows[0]['receipt_item_id']) + '/resolve'
        product = self.client.post(url, json=payload).json()['product']
        self.assertEqual(product['upc'], '71928395643')
        self.assertEqual(product['notes'], payload['notes'])
        self.assertEqual(product['unit'], 'bag')
        self.assertEqual(self.client.get('/api/unknown-products').json(), [])
        self.assertEqual(self.client.get('/api/dashboard').json()['products'][0]['inventory_quantity'], 4)
        self.client.post(url, json={'name':'Changed'})
        duplicate = self.client.post('/api/products', json={'upc':'719-28395643', 'name':'Changed'}).json()
        self.assertEqual(duplicate['id'], product['id'])
        self.assertEqual(duplicate['name'], product['name'])
        with patch('app.lookup_open_food_facts', new_callable=AsyncMock) as external:
            future = {'parsed': {'fingerprint':'future', 'items':[{'raw_code':'71928395643'}]},
                      'selected_products':{'71928395643':{'name':'Stale External Name'}}}
            self.assertEqual(self.client.post('/api/receipts/import', json=future).status_code, 200)
            external.assert_not_awaited()
        dashboard = self.client.get('/api/dashboard').json()
        self.assertEqual(len(dashboard['products']), 1)
        self.assertEqual(dashboard['products'][0]['name'], product['name'])
        self.assertEqual(dashboard['products'][0]['inventory_quantity'], 5)

    def test_concurrent_product_saves_share_unique_upc(self):
        from concurrent.futures import ThreadPoolExecutor
        def save(name):
            with TestClient(app) as client:
                return client.post('/api/products', json={'upc':'007-1928395643', 'name':name})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, ['First', 'Second']))
        self.assertTrue(all(result.status_code == 200 for result in results))
        self.assertEqual(results[0].json()['id'], results[1].json()['id'])
        self.assertEqual(results[0].json()['name'], results[1].json()['name'])
        self.assertEqual(len(self.client.get('/api/dashboard').json()['products']), 1)

    def test_concurrent_known_receipts_add_inventory_without_losing_quantities(self):
        from concurrent.futures import ThreadPoolExecutor
        self.client.post('/api/products', json={'upc':'71928395643', 'name':'Vegetables'})
        def purchase(index):
            with TestClient(app) as client:
                return client.post('/api/receipts/import', json={'parsed': {
                    'fingerprint':'concurrent-' + str(index), 'items':[{'upc':'71928395643', 'quantity':2}]}})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(purchase, [1, 2]))
        self.assertTrue(all(result.status_code == 200 for result in results))
        self.assertEqual(self.client.get('/api/dashboard').json()['products'][0]['inventory_quantity'], 4)

    def test_invalid_receipt_does_not_save_selected_products(self):
        payload = {'parsed': {'fingerprint':'bad', 'items':[{'raw_code':'71928395643', 'quantity':-1}]},
                   'selected_products': {'71928395643':{'name':'Must Not Save'}}}
        self.assertEqual(self.client.post('/api/receipts/import', json=payload).status_code, 400)
        self.assertEqual(self.client.get('/api/dashboard').json()['products'], [])

    def test_browser_invalid_receipts(self):
        headers = {'Authorization': 'Bearer test-token'}
        for text, expected in [(None, 400), ('', 400), ('Not a receipt', 422), ('x' * 500001, 413)]:
            response = self.client.post('/api/receipts/browser', json={'text': text}, headers=headers)
            self.assertEqual(response.status_code, expected)



class SchemaMigrationTests(unittest.TestCase):
    def legacy_database(self):
        db_engine = create_engine('sqlite:///:memory:')
        with db_engine.begin() as connection:
            connection.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, receipt_code_raw TEXT UNIQUE, gtin_normalized TEXT, name TEXT)"))
            connection.execute(text("CREATE TABLE receipt_items (id INTEGER PRIMARY KEY, raw_code TEXT, normalized_code TEXT, product_id INTEGER, quantity FLOAT)"))
            connection.execute(text("CREATE TABLE inventory (id INTEGER PRIMARY KEY, product_id INTEGER, quantity FLOAT)"))
            connection.execute(text("INSERT INTO products VALUES (1, '71928395643', '071928395643', 'Mixed Frozen Vegetables')"))
            connection.execute(text("INSERT INTO receipt_items VALUES (1, '71928395643', '071928395643', 1, 3)"))
            connection.execute(text("INSERT INTO inventory VALUES (1, 1, 3)"))
        self.addCleanup(db_engine.dispose)
        return db_engine

    def test_additive_migration_preserves_existing_data_and_is_idempotent(self):
        db_engine = self.legacy_database()
        initialize_database(db_engine)
        initialize_database(db_engine)
        with db_engine.connect() as connection:
            product = connection.execute(text('SELECT * FROM products')).mappings().one()
            self.assertEqual(product['name'], 'Mixed Frozen Vegetables')
            self.assertEqual(product['upc'], '71928395643')
            self.assertEqual(product['receipt_code_raw'], '71928395643')
            self.assertEqual(product['gtin_normalized'], '071928395643')
            self.assertEqual(connection.execute(text('SELECT normalized_code FROM receipt_items')).scalar(), '71928395643')
            self.assertEqual(connection.execute(text('SELECT quantity FROM inventory')).scalar(), 3)
        indexes = inspect(db_engine).get_indexes('products')
        self.assertTrue(any(index['unique'] and index['column_names'] == ['upc'] for index in indexes))
        self.assertIn('product_lookup_cache', inspect(db_engine).get_table_names())

    def test_collision_migration_stops_without_destroying_records(self):
        db_engine = self.legacy_database()
        with db_engine.begin() as connection:
            connection.execute(text("INSERT INTO products VALUES (2, '719-28395643', '071928395643', 'Other Name')"))
        with self.assertRaisesRegex(RuntimeError, 'preserved'):
            initialize_database(db_engine)
        with db_engine.connect() as connection:
            self.assertEqual(connection.execute(text('SELECT COUNT(*) FROM products')).scalar(), 2)
        self.assertNotIn('upc', {column['name'] for column in inspect(db_engine).get_columns('products')})


if __name__ == '__main__':
    unittest.main()

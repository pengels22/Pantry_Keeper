"""Recipe integrity tests use the existing temporary-database test bootstrap."""
import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import unittest
import httpx
from fastapi import HTTPException
from sqlalchemy import create_engine, text, inspect
import test_workflow  # Sets DATABASE_URL to a temporary DB before importing app.
from app import app
from fastapi.testclient import TestClient
from db import Base, engine, SessionLocal
from models import Product, Inventory, InventoryTransaction
from services import recipe_inventory as stock
from services.units import normalize_unit, convert_amount
from services.openai_recipes import execute_tool, recipe_chat, TOOLS
from services.recipe_schemas import ChatRequest
from services.schema import initialize_database


class RecipeTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        self.client = TestClient(app)
        self.ids = []
        for index, (name, amount, unit) in enumerate([
            ('Chicken Breast', 4, 'each'), ('Pasta', 16, 'oz'), ('Parmesan', 9, 'oz')]):
            with SessionLocal() as db:
                product = Product(upc=f'1234567890{index}', receipt_code_raw=f'1234567890{index}', name=name, category='Dinner')
                db.add(product); db.flush()
                item = Inventory(product_id=product.id, quantity=1, usable_quantity=amount, usable_unit=unit)
                db.add(item); db.commit(); self.ids.append(item.id)
        self.proposal = {'recipe': 'Chicken Pasta', 'instructions': 'Cook chicken thoroughly and combine with cooked pasta.', 'ingredients': [
            {'inventory_id': self.ids[0], 'name': 'Wrong model name', 'amount': 2, 'unit': 'each'},
            {'inventory_id': self.ids[1], 'amount': 8, 'unit': 'oz'},
            {'inventory_id': self.ids[2], 'amount': 3, 'unit': 'oz'}]}

    def test_inventory_html_exports_all_fields_and_escapes_values(self):
        with SessionLocal() as db:
            product = db.query(Product).first()
            product.notes = '<script>alert("test")</script>'
            db.commit()
        response = self.client.get('/api/inventory?format=html&limit=1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.headers['content-type'])
        self.assertIn('inventory.reserved_quantity', response.text)
        self.assertIn('product.notes', response.text)
        self.assertIn('product.upc', response.text)
        for name in ('Chicken Breast', 'Pasta', 'Parmesan'):
            self.assertIn(name, response.text)
        self.assertNotIn('<script>', response.text)
        self.assertIn('&lt;script&gt;', response.text)
        self.assertIsInstance(self.client.get('/api/inventory').json(), list)
        with SessionLocal() as db:
            db.query(Inventory).delete()
            db.commit()
        self.assertIn('No inventory items.', self.client.get('/api/inventory?format=html').text)

    def test_full_inventory_json_export(self):
        response = self.client.get('/api/inventory?format=export-json&limit=1')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 3)
        self.assertIsInstance(data[0]['inventory.quantity'], (int, float))
        self.assertIn('product.notes', data[0])
        self.assertIsNone(data[0]['product.notes'])
        self.assertIn('product.created_at', data[0])

    def select(self, proposal=None):
        response = self.client.post('/api/recipes/session', json={'proposal': proposal or self.proposal})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def reserve(self, session_id):
        response = self.client.post(f'/api/recipes/{session_id}/reserve')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def usage(self, amounts=(2, 8, 3)):
        return [{'inventory_id': item_id, 'amount': amount, 'unit': unit} for item_id, amount, unit in
            zip(self.ids, amounts, ['each', 'oz', 'oz'])]

    def balances(self):
        return [self.client.get(f'/api/inventory/{item_id}').json() for item_id in self.ids]

    def test_end_to_end_reserve_review_commit_history(self):
        self.assertEqual(self.client.get('/recipes').status_code, 200)
        selected = self.select()
        self.assertEqual([item['usable_quantity'] for item in self.balances()], [4, 16, 9])
        reserved = self.reserve(selected)
        self.assertEqual(reserved['status'], 'reserved')
        self.assertEqual(reserved['items'][0]['name'], 'Chicken Breast')
        self.assertEqual([item['usable_quantity'] for item in self.balances()], [4, 16, 9])
        self.assertEqual([item['reserved_quantity'] for item in self.balances()], [2, 8, 3])
        self.assertEqual(self.client.post(f'/api/recipes/{selected}/commit', json={'actual_usage': self.usage()}).status_code, 422)
        response = self.client.post(f'/api/recipes/{selected}/commit', json={'confirmed': True, 'actual_usage': self.usage()})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'completed')
        self.assertEqual([item['usable_quantity'] for item in self.balances()], [2, 8, 6])
        self.assertEqual([item['reserved_quantity'] for item in self.balances()], [0, 0, 0])
        for item_id, amount in zip(self.ids, [2, 8, 3]):
            history = self.client.get(f'/api/inventory/{item_id}/transactions').json()
            self.assertEqual([row['transaction_type'] for row in history], ['recipe_consumption', 'recipe_release', 'recipe_reservation'])
            self.assertEqual(history[0]['change_amount'], -amount)
        self.assertEqual(self.client.post(f'/api/recipes/{selected}/commit', json={'confirmed': True, 'actual_usage': self.usage()}).status_code, 409)

    def test_confirmation_is_boolean_and_invalid_commit_ids_do_not_mutate(self):
        selected = self.select(); self.reserve(selected)
        for confirmation in (False, 1, "true"):
            response = self.client.post(f'/api/recipes/{selected}/commit', json={'confirmed': confirmation, 'actual_usage': self.usage()})
            self.assertEqual(response.status_code, 422, response.text)
        actual = self.usage(); actual[0]['inventory_id'] = 9999
        self.assertEqual(self.client.post(f'/api/recipes/{selected}/commit', json={'confirmed': True, 'actual_usage': actual}).status_code, 404)
        self.assertEqual(self.client.get(f'/api/recipes/{selected}').json()['status'], 'reserved')
        self.assertEqual([item['usable_quantity'] for item in self.balances()], [4, 16, 9])

    def test_existing_recipe_comparison_respects_measured_available_stock(self):
        selected = self.select({'recipe': 'Chicken', 'ingredients': [{'inventory_id': self.ids[0], 'amount': 4, 'unit': 'each'}]})
        self.reserve(selected)
        result = self.client.post('/api/recipes/compare', json={'text': 'Chicken Breast'}).json()
        self.assertEqual(len(result['in_stock']), 0)
        self.assertEqual(len(result['shopping_list']), 1)

    def test_cancel_releases_without_consuming(self):
        selected = self.select(); self.reserve(selected)
        self.assertEqual(self.client.post(f'/api/recipes/{selected}/cancel').status_code, 200)
        self.assertEqual([item['usable_quantity'] for item in self.balances()], [4, 16, 9])
        self.assertEqual([item['reserved_quantity'] for item in self.balances()], [0, 0, 0])
        self.assertEqual(self.client.post(f'/api/recipes/{selected}/reserve').status_code, 409)
        self.assertEqual(self.client.post(f'/api/recipes/{selected}/cancel').status_code, 409)

    def test_actual_usage_edits_and_zero(self):
        selected = self.select(); self.reserve(selected)
        response = self.client.post(f'/api/recipes/{selected}/commit', json={'confirmed': True, 'actual_usage': self.usage((1, 0, 4))})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item['usable_quantity'] for item in self.balances()], [3, 16, 5])
        self.assertEqual([item['reserved_quantity'] for item in self.balances()], [0, 0, 0])

    def test_failed_commit_rolls_back_all_items_history_and_status(self):
        selected = self.select(); self.reserve(selected)
        before = [self.client.get(f'/api/inventory/{item_id}/transactions').json() for item_id in self.ids]
        response = self.client.post(f'/api/recipes/{selected}/commit', json={'confirmed': True, 'actual_usage': self.usage((1, 8, 99))})
        self.assertEqual(response.status_code, 409)
        self.assertEqual([item['usable_quantity'] for item in self.balances()], [4, 16, 9])
        self.assertEqual([item['reserved_quantity'] for item in self.balances()], [2, 8, 3])
        self.assertEqual(self.client.get(f'/api/recipes/{selected}').json()['status'], 'reserved')
        self.assertEqual(before, [self.client.get(f'/api/inventory/{item_id}/transactions').json() for item_id in self.ids])

    def test_reserve_revalidates_and_rolls_back_partial_holds(self):
        selected = self.select()
        other = self.select({'recipe': 'Other', 'ingredients': [{'inventory_id': self.ids[2], 'amount': 8, 'unit': 'oz'}]})
        self.reserve(other)
        self.assertEqual(self.client.post(f'/api/recipes/{selected}/reserve').status_code, 409)
        self.assertEqual([item['reserved_quantity'] for item in self.balances()], [0, 0, 8])
        self.assertEqual(self.client.get(f'/api/recipes/{selected}').json()['status'], 'selected')

    def test_other_reservations_are_protected(self):
        selected = self.select(); other = self.select(); self.reserve(selected); self.reserve(other)
        response = self.client.post(f'/api/recipes/{selected}/commit', json={'confirmed': True, 'actual_usage': self.usage((3, 8, 3))})
        self.assertEqual(response.status_code, 409)
        self.assertEqual([item['reserved_quantity'] for item in self.balances()], [4, 16, 6])
        self.assertEqual(self.client.put(f'/api/inventory/{self.ids[0]}/measurements', json={'usable_quantity': 5, 'usable_unit': 'each'}).status_code, 409)

    def test_proposal_validation_and_read_only_tools(self):
        before = self.balances()
        with SessionLocal() as db:
            args = {**self.proposal, 'ingredients': [{**item, 'name': item.get('name', ''), 'notes': ''} for item in self.proposal['ingredients']]}
            result = execute_tool(db, 'propose_inventory_usage', args)
            self.assertEqual(result['ingredients'][0]['name'], 'Chicken Breast')
            self.assertEqual(db.query(InventoryTransaction).count(), 0)
            with self.assertRaises(HTTPException): execute_tool(db, 'subtract_inventory', {'inventory_id': self.ids[0], 'amount': 1})
        self.assertEqual(before, self.balances())
        for ingredient, status in [
            ({'inventory_id': 9999, 'amount': 1, 'unit': 'oz'}, 404),
            ({'inventory_id': self.ids[0], 'amount': 5, 'unit': 'each'}, 409),
            ({'inventory_id': self.ids[0], 'amount': 1, 'unit': 'oz'}, 400),
            ({'inventory_id': self.ids[0], 'amount': -1, 'unit': 'each'}, 422),
            ({'inventory_id': self.ids[0], 'amount': True, 'unit': 'each'}, 422),
            ({'inventory_id': True, 'amount': 1, 'unit': 'each'}, 422),
            ({'inventory_id': self.ids[0], 'amount': 0, 'unit': 'each'}, 422),
        ]:
            response = self.client.post('/api/recipes/propose', json={'recipe': 'Test', 'ingredients': [ingredient]})
            self.assertEqual(response.status_code, status, response.text)
        duplicated = {**self.proposal, 'ingredients': [self.proposal['ingredients'][0]] * 2}
        self.assertEqual(self.client.post('/api/recipes/propose', json=duplicated).status_code, 400)

    def test_add_subtract_undo_history_prevent_negative(self):
        item_id = self.ids[1]
        add = self.client.post(f'/api/inventory/{item_id}/add', json={'confirmed': True, 'amount': 1, 'unit': 'lb'})
        self.assertEqual(add.status_code, 200, add.text)
        self.assertEqual(add.json()['inventory']['usable_quantity'], 32)
        subtract = self.client.post(f'/api/inventory/{item_id}/subtract', json={'confirmed': True, 'amount': 8, 'unit': 'oz'})
        self.assertEqual(subtract.status_code, 200)
        self.assertEqual(self.client.post(f'/api/inventory/{item_id}/subtract', json={'confirmed': True, 'amount': 99, 'unit': 'oz'}).status_code, 409)
        undo_id = subtract.json()['transaction_id']
        undo = self.client.post(f'/api/inventory/transactions/{undo_id}/undo', json={'confirmed': True})
        self.assertEqual(undo.status_code, 200, undo.text)
        self.assertEqual(undo.json()['inventory']['usable_quantity'], 32)
        self.assertEqual(self.client.post(f'/api/inventory/transactions/{undo_id}/undo', json={'confirmed': True}).status_code, 409)
        with SessionLocal() as db:
            with self.assertRaises(HTTPException): stock.subtract_inventory(db, item_id, float('nan'), 'oz')
            with self.assertRaises(HTTPException): stock.add_inventory(db, 9999, 1, 'oz')

    def test_measurement_configuration_and_legacy_purchases(self):
        item_id = self.ids[1]
        response = self.client.put(f'/api/inventory/{item_id}/measurements', json={
            'package_quantity': 2, 'package_size': 16, 'package_unit': 'ounces', 'usable_unit': 'oz'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['usable_quantity'], 32)
        self.assertEqual(response.json()['package_quantity'], 2)
        from services.inventory_service import add_to_inventory
        with SessionLocal() as db:
            row = stock.get_row(db, item_id)
            add_to_inventory(db, db.get(Product, row.product_id), 1); db.commit()
        row = self.client.get(f'/api/inventory/{item_id}').json()
        self.assertEqual(row['quantity'], 3)
        self.assertEqual(row['package_quantity'], 3)
        self.assertEqual(row['usable_quantity'], 48)
        self.assertEqual(self.client.post(f"/api/inventory/{row['product_id']}", json={'quantity': 2}).status_code, 200)
        self.assertEqual(self.client.get(f'/api/inventory/{item_id}').json()['usable_quantity'], 32)
        self.assertEqual(self.client.put(f'/api/inventory/{item_id}/measurements', json={
            'package_quantity': 1, 'package_size': 48, 'package_unit': 'oz', 'usable_unit': 'cup'}).status_code, 400)

    def test_unconfigured_inventory_is_not_guessed(self):
        with SessionLocal() as db:
            row = db.get(Inventory, self.ids[0]); row.usable_quantity = None; row.usable_unit = None; db.commit()
        self.assertEqual(self.client.post('/api/recipes/propose', json=self.proposal).status_code, 409)
        self.assertTrue(self.client.get(f'/api/inventory/{self.ids[0]}').json()['measurement_required'])

    def test_session_planning_selection_and_search(self):
        planning = self.client.post('/api/recipes/session', json={'title': 'Dinner'}).json()
        self.assertEqual(planning['status'], 'planning')
        self.assertEqual(self.client.post(f"/api/recipes/{planning['id']}/reserve").status_code, 409)
        self.assertEqual(self.client.post(f"/api/recipes/{planning['id']}/select", json=self.proposal).status_code, 200)
        self.assertEqual(len(self.client.get('/api/inventory/search?q=Chicken').json()), 1)
        self.assertEqual(len(self.client.get('/api/inventory?category=Dinner').json()), 3)
        self.assertEqual(self.client.get('/api/inventory/999').status_code, 404)

    def test_concurrent_reservations_do_not_oversell(self):
        proposal = {'recipe': 'Chicken', 'ingredients': [{'inventory_id': self.ids[0], 'amount': 3, 'unit': 'each'}]}
        first, second = self.select(proposal), self.select(proposal)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda session_id: self.client.post(f'/api/recipes/{session_id}/reserve'), [first, second]))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        self.assertEqual(self.balances()[0]['reserved_quantity'], 3)

    def test_double_reserve_and_concurrent_commit_are_once_only(self):
        selected = self.select(); self.reserve(selected)
        self.assertEqual(self.client.post(f'/api/recipes/{selected}/reserve').status_code, 409)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.client.post(f'/api/recipes/{selected}/commit',
                json={'confirmed': True, 'actual_usage': self.usage()}), range(2)))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        self.assertEqual([item['usable_quantity'] for item in self.balances()], [2, 8, 6])

    def test_missing_key_and_mocked_responses_tool_loop(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            self.assertEqual(self.client.post('/api/recipes/chat', json={'message': 'Dinner?'}).status_code, 503)
        requests = []
        proposal = {**self.proposal, 'ingredients': [{**item, 'name': item.get('name', ''), 'notes': ''} for item in self.proposal['ingredients']]}
        def handle(request):
            data = json.loads(request.content); requests.append(data)
            self.assertEqual(data['store'], False)
            if len(requests) == 1:
                output = [{'type': 'function_call', 'call_id': 'read', 'name': 'get_inventory', 'arguments': '{"offset":0,"limit":200}'}]
            elif len(requests) == 2:
                output = [{'type': 'function_call', 'call_id': 'proposal', 'name': 'propose_inventory_usage', 'arguments': json.dumps(proposal)}]
            else:
                output = [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Try chicken pasta.'}]}]
            return httpx.Response(200, json={'output': output})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                with SessionLocal() as db:
                    return await recipe_chat(db, ChatRequest(message='Dinner?', history=[]), client=client)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-server-key'}): result = asyncio.run(run())
        self.assertEqual(result['proposal']['recipe'], 'Chicken Pasta')
        self.assertEqual(len(requests), 3)
        self.assertEqual([item['usable_quantity'] for item in self.balances()], [4, 16, 9])
        self.assertEqual(len(TOOLS), 5)
        self.assertNotIn('test-server-key', self.client.get('/recipes').text)


class UnitAndMigrationTests(unittest.TestCase):
    def test_units_aliases_conversions_and_incompatible_dimensions(self):
        for alias, canonical in [('ounces','oz'), ('pounds','lb'), ('tablespoons','tbsp'), ('teaspoon','tsp'), ('liters','L')]:
            self.assertEqual(normalize_unit(alias), canonical)
        self.assertEqual(convert_amount(1, 'lb', 'oz'), 16)
        self.assertEqual(convert_amount(1, 'cup', 'tbsp'), 16)
        self.assertEqual(convert_amount(1, 'L', 'ml'), 1000)
        with self.assertRaises(HTTPException): convert_amount(1, 'oz', 'cup')
        with self.assertRaises(HTTPException): normalize_unit('box')

    def test_additive_inventory_migration_preserves_ambiguous_data(self):
        db_engine = create_engine('sqlite:///:memory:')
        try:
            with db_engine.begin() as connection:
                connection.execute(text('CREATE TABLE inventory (id INTEGER PRIMARY KEY, product_id INTEGER, quantity FLOAT)'))
                connection.execute(text('INSERT INTO inventory VALUES (1, 42, 2.75)'))
            initialize_database(db_engine); initialize_database(db_engine)
            with db_engine.connect() as connection:
                row = connection.execute(text('SELECT * FROM inventory')).mappings().one()
                self.assertEqual(row['quantity'], 2.75)
                self.assertIsNone(row['usable_quantity'])
                self.assertIsNone(row['package_quantity'])
                self.assertIsNone(row['package_size'])
                self.assertEqual(row['reserved_quantity'], 0)
            self.assertTrue({'inventory_transactions','recipe_sessions','recipe_session_items'} <= set(inspect(db_engine).get_table_names()))
        finally: db_engine.dispose()

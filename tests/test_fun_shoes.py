"""Customer messages drive these journeys; no complete model ACTION fixtures."""
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import main, shop
from backend.actions import ProposalStore, OperationStore
from backend.sessions import SessionManager
from backend.order_support import NOT_FOUND


class FunShoesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = self.tmp.name + '/shop.db'
        connection = shop.connect(self.path)
        shop.seed(connection)
        connection.close()
        for name, value in [('manager', SessionManager()), ('proposals', ProposalStore()),
                            ('operations', OperationStore()),
                            ('shop_connection', lambda: shop.connect(self.path))]:
            patcher = patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)
        self.client.post('/sessions', json={'customer_id': 'demo_maya'})

    def chat(self, message):
        result = self.client.post('/chat', json={'message': message})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_missing_unknown_corrected_ids_for_every_intent(self):
        for intent in ['return', 'exchange', 'cancel']:
            with self.subTest(intent=intent):
                self.client.post('/sessions/reset')
                first = self.chat(f'I want to {intent} my shoes')
                self.assertIn('What is your FUN SHOES order ID?', first['response'])
                self.assertIsNone(first['action_proposal'])
                bad = self.chat('order_missing_999')
                self.assertEqual(bad['response'], NOT_FOUND)
                self.assertIsNone(bad['action_proposal'])
                self.assertEqual(len(main.proposals._proposals), 0)
                good = self.chat(' ORDER_MAYA_1 ' if intent == 'cancel' else 'order_maya_3')
                self.assertNotEqual(good['response'], NOT_FOUND)
                if intent == 'cancel':
                    self.assertEqual(good['action_proposal']['order_id'], 'order_maya_1')
                else:
                    self.assertIn('unworn', good['response'])
                self.client.post('/sessions/reset')

    def test_return_full_conversation_and_confirmation(self):
        self.chat('I want to return my shoes')
        self.chat('order_maya_3')
        self.assertIn('reason', self.chat('unworn')['response'])
        proposal = self.chat('does not fit')['action_proposal']
        self.assertEqual(proposal['kind'], 'return')
        self.assertIn('Fjell Trek', proposal['terms'])
        result = self.client.post(f"/actions/{proposal['proposal_id']}/confirm")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()['return_reference'].startswith('ret_'))
        session = next(iter(main.manager._sessions.values()))
        self.assertIsNone(session.order_context)

    def test_exchange_by_size_without_internal_ids(self):
        self.chat('exchange order_maya_3')
        self.assertIn('replacement size', self.chat('unworn')['response'])
        proposal = self.chat('38')['action_proposal']
        self.assertEqual(proposal['replacement_variant_id'], 'var_fjell_38')
        result = self.client.post(f"/actions/{proposal['proposal_id']}/confirm")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()['exchange_reference'].startswith('exg_'))

    def test_initial_id_and_shipped_denial(self):
        self.assertIn('cannot be cancelled', self.chat('cancel order_maya_2')['response'])
        self.assertEqual(len(main.proposals._proposals), 0)
        proposal = self.chat('cancel order_maya_1')['action_proposal']
        self.assertIsNotNone(proposal)
        with shop.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT state FROM orders WHERE id='order_maya_1'").fetchone()[0], 'processing')
        self.assertEqual(self.client.post(f"/actions/{proposal['proposal_id']}/confirm").status_code, 200)

    def test_other_customer_non_disclosing(self):
        first = self.chat('cancel order_leo_1')
        second = self.chat('order_missing_999')
        self.assertEqual(first['response'], second['response'])
        self.assertEqual(first['response'], NOT_FOUND)

    def test_obsolete_proposal_invalidated_by_target_switch_and_reset(self):
        proposal = self.chat('cancel order_maya_1')['action_proposal']
        self.chat('order_maya_2')
        self.assertEqual(self.client.post(f"/actions/{proposal['proposal_id']}/confirm").status_code, 404)
        proposal = self.chat('cancel order_maya_1')['action_proposal']
        self.client.post('/sessions/reset')
        self.assertEqual(self.client.post(f"/actions/{proposal['proposal_id']}/confirm").status_code, 404)
        self.assertIsNone(next(iter(main.manager._sessions.values())).order_context)

    def test_customer_switch_and_expiry(self):
        proposal = self.chat('cancel order_maya_1')['action_proposal']
        self.client.post('/sessions/customer', json={'customer_id': 'demo_leo'})
        self.assertEqual(self.client.post(f"/actions/{proposal['proposal_id']}/confirm").status_code, 404)
        self.assertIn('order ID', self.chat('cancel my order')['response'])
        main.manager.idle_timeout_seconds = -1
        self.assertEqual(self.client.post('/chat', json={'message': 'order_maya_1'}).status_code, 403)

    def test_voice_ambiguity_does_not_guess(self):
        self.chat('cancel my order')
        self.assertIn('type it exactly', self.chat('order maya one')['response'])
        self.assertEqual(len(main.proposals._proposals), 0)

    def test_general_policy_and_other_store_scope(self):
        with patch('backend.agent.generate_response', return_value='FUN SHOES returns within 30 days.') as generate:
            result = self.chat('What is the FUN SHOES returns policy?')
            self.assertTrue(result['sources'])
            self.assertTrue(generate.called)
        result = self.chat('Cancel my Amazon order order_maya_1')
        self.assertIn('FUN SHOES only', result['response'])
        self.assertIsNone(result['action_proposal'])

    def test_catalog_and_customer_scoped_demo_orders(self):
        catalog = self.client.get('/catalog').json()
        self.assertEqual(catalog['store'], 'FUN SHOES')
        self.assertEqual(len(catalog['variants']), 18)
        with shop.connect(self.path) as connection:
            connection.execute("UPDATE inventory SET on_hand = 8 WHERE variant_id = 'var_summit_39'")
        changed = self.client.get('/catalog').json()['variants']
        self.assertEqual(next(v for v in changed if v['id'] == 'var_summit_39')['available'], 7)
        orders = self.client.get('/demo/orders').json()['orders']
        self.assertTrue(all(o['id'].startswith('order_maya_') for o in orders))
        self.assertNotIn('order_missing_999', [o['id'] for o in orders])
        html = self.client.get('/').text
        self.assertIn('Shop FUN SHOES', html)
        self.assertNotIn('Voice Agent Explorer', html)

    def test_model_cannot_invent_order_action(self):
        with patch('backend.agent.generate_response', return_value='Done\nACTION {"tool":"propose_cancellation","args":{"order_id":"order_maya_1"}}'):
            self.assertIsNone(self.chat('hello')['action_proposal'])
        self.assertEqual(len(main.proposals._proposals), 0)

    def test_unknown_store_fact_abstains_without_model_invention(self):
        with patch('backend.agent.generate_response', return_value='A made-up warranty') as generate:
            result = self.chat('Does FUN SHOES offer a lifetime warranty for wedding heels?')
            self.assertIn("don't have FUN SHOES knowledge", result['response'])
            generate.assert_not_called()
            self.assertIsNone(result['action_proposal'])

    def test_product_selection_answers_from_database(self):
        with patch('backend.agent.generate_response') as generate:
            result = self.chat('Tell me about Fjell Trek at FUN SHOES')
            self.assertIn('€159.00', result['response'])
            self.assertIn('EU 40', result['response'])
            generate.assert_not_called()

    def test_fixture_reset_after_confirmed_return(self):
        proposal = self.chat('return order_maya_3 unworn does not fit')['action_proposal']
        self.assertEqual(self.client.post(f"/actions/{proposal['proposal_id']}/confirm").status_code, 200)
        connection = shop.connect(self.path)
        self.addCleanup(connection.close)
        shop.seed(connection)
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM returns').fetchone()[0], 0)
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 8)

    def test_ambiguous_order_line_asks_for_item(self):
        with shop.connect(self.path) as connection:
            connection.execute("INSERT INTO order_lines VALUES (?, ?, ?, ?, ?, ?, ?)",
                               ('order_maya_3', 'var_fjell_38', 'Fjell Trek', 38, 'brown', 15900, 1))
        response = self.chat('exchange order_maya_3 for size 38')
        self.assertIn('Which item?', response['response'])
        self.assertIsNone(response['action_proposal'])
        response = self.chat('original size 39')
        self.assertIn('unworn', response['response'])

"""Automated behavior tests; mock paid external APIs, never charge cards."""
import base64
import io
import os
import time
import unittest
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
import server


class LiveStoreTests(unittest.TestCase):
    def setUp(self):
        self.keys = patch.dict(os.environ, {
            'OPENAI_API_KEY': 'test-openai-secret',
            'STRIPE_SECRET_KEY': 'sk_test_placeholder',
            'APP_SIGNING_SECRET': 'test-signing-key-long-enough-for-test-only',
            'ADMIN_TOKEN': 'admin-private-test-token',
            'TURNSTILE_SITE_KEY': 'test-site-key',
            'TURNSTILE_SECRET_KEY': 'test-turnstile-secret',
            'PREVIEW_LIMIT_DAILY': '5',
        })
        self.keys.start()
        self.addCleanup(self.keys.stop)
        self.client = TestClient(server.app)
        server._attempts.clear()
        server._daily_counter.update(date='', total=0)
        self.email = 'buyer@example.test'
        self.sample_payload = {'email': self.email, 'idea': 'A superhero uses X-ray vision to find his car keys in his own pocket.',
                               'style': 'Cinematic comedy', 'turnstile_token': 'test-captcha'}

    def test_site_works_without_db(self):
        for path in ['/', '/order', '/admin', '/static/app.css', '/static/optv-logo.webp', '/healthz']:
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertEqual(self.client.get('/api/config').json()['preview_enabled'], True)

    def test_bad_admin_token_is_rejected(self):
        self.assertEqual(self.client.get('/api/admin/orders').status_code, 401)
        self.assertEqual(self.client.get('/api/admin/orders', headers={'Authorization': 'Bearer wrong'}).status_code, 401)

    def test_unconfigured_refuses_billable_generation(self):
        os.environ.pop('OPENAI_API_KEY')
        r = self.client.post('/api/preview', json=self.sample_payload)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self.client.get('/api/config').json()['preview_enabled'], False)

    def test_preview_captcha_blocks_request(self):
        with patch.object(server, 'captcha', side_effect=server.HTTPException(403, 'denied')):
            self.assertEqual(self.client.post('/api/preview', json=self.sample_payload).status_code, 403)

    def test_preview_is_branded_signed_and_one_per_email(self):
        state = {'status': 'new', 'ts': '0', 'meter_date': '', 'meter_count': '0'}
        def fake_stripe(method, path, fields=None, **kwargs):
            if method == 'GET' and path == '/customers':
                if fields and fields.get('email') == server.METER_EMAIL:
                    return {'data': [{'id': 'cus_meter', 'metadata': {
                        'optv_internal_meter': 'daily_previews',
                        'optv_preview_day': state['meter_date'],
                        'optv_preview_count': state['meter_count']}}]}
                return {'data': [{'id': 'cus_1', 'metadata': {
                    'optv_sample_status': state['status'], 'optv_sample_updated': state['ts']}}]}
            if method == 'POST' and path == '/customers/cus_meter':
                state['meter_date'] = fields['metadata[optv_preview_day]']
                state['meter_count'] = fields['metadata[optv_preview_count]']
                return {'id': 'cus_meter'}
            if method == 'POST' and path == '/customers/cus_1':
                state['status'] = fields['metadata[optv_sample_status]']
                state['ts'] = fields['metadata[optv_sample_updated]']
                return {'id': 'cus_1'}
            self.fail('Unexpected Stripe request '+path)
        output = io.BytesIO()
        Image.new('RGB', (1024,1536), (116,44,92)).save(output, 'PNG')
        with patch.object(server,'captcha'), patch.object(server,'stripe',side_effect=fake_stripe),patch.object(server,'generate_scene',return_value=server.brand_image(output.getvalue(),'anything','Cinematic comedy')):
            r = self.client.post('/api/preview',json=self.sample_payload)
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertEqual(state['status'], 'used')
            self.assertEqual(state['meter_count'], '1')
            self.assertTrue(data['preview_image'].startswith('data:image/jpeg;base64,'))
            raw = base64.b64decode(data['preview_image'].split(',')[1])
            self.assertGreater(len(raw), 20000)
            parsed = server.read_token(data['preview_token'])
            self.assertEqual(parsed['e'], self.email)
            r2 = self.client.post('/api/preview',json=self.sample_payload)
            self.assertEqual(r2.status_code,409)

    def test_daily_preview_budget_persists_after_restart(self):
        os.environ['PREVIEW_LIMIT_DAILY'] = '1'
        state = {'day': '', 'count': '0'}
        def fake_stripe(method, path, fields=None, **kwargs):
            if method == 'GET' and path == '/customers':
                if fields['email'] == server.METER_EMAIL:
                    return {'data': [{'id': 'cus_meter', 'metadata': {
                        'optv_internal_meter': 'daily_previews',
                        'optv_preview_day': state['day'], 'optv_preview_count': state['count']}}]}
                return {'data': [{'id': 'cus_free', 'metadata': {'optv_sample_status': 'new'}}]}
            if method == 'POST' and path == '/customers/cus_meter':
                state['day'] = fields['metadata[optv_preview_day]']
                state['count'] = fields['metadata[optv_preview_count]']
                return {}
            if method == 'POST' and path == '/customers/cus_free':
                return {}
            self.fail('Unexpected Stripe path: ' + path)
        with patch.object(server, 'captcha'), patch.object(server, 'stripe', side_effect=fake_stripe), \
             patch.object(server, 'generate_scene', return_value=b'jpeg-bytes'), \
             patch.object(server, 'rate_limit'):
            first = self.client.post('/api/preview', json=self.sample_payload)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(state['count'], '1')
            # Render Free instance may sleep/restart; shared Stripe metadata survives.
            server._attempts.clear()
            server._daily_counter.update(date='', total=0)
            second = self.client.post('/api/preview', json={**self.sample_payload, 'email': 'second@example.test'})
            self.assertEqual(second.status_code, 429, second.text)
            self.assertEqual(state['count'], '1')

    def test_forged_checkout_token_rejected_without_calling_stripe(self):
        payload=server.make_token({'v':1,'e':self.email,'i':'Fun original comedy idea','s':'Cinematic comedy','t':int(time.time())})
        forged=payload[:-4]+'FAKE'
        with patch.object(server,'stripe') as api:
            r=self.client.post('/api/checkout',json={'preview_token':forged,'instructions':'hello'})
            self.assertEqual(r.status_code,400)
            api.assert_not_called()

    def test_valid_checkout_pricing_fixed_in_server_and_stripe_redirect(self):
        token=server.make_token({'v':1,'e':self.email,'i':self.sample_payload['idea'],'s':'Cinematic comedy','t':int(time.time())})
        def api(method,path,fields=None,**kwargs):
            self.assertEqual(fields['line_items[0][price_data][unit_amount]'],'2999')
            self.assertEqual(fields['metadata[idea]'],self.sample_payload['idea'])
            self.assertEqual(fields['metadata[optv_product]'],server.PRODUCT_KEY)
            return {'url':'https://checkout.stripe.com/c/pay/test-session'}
        with patch.object(server,'stripe',side_effect=api):
            r=self.client.post('/api/checkout',json={'preview_token':token,'instructions':'Keep it goofy','price_cents':1})
            self.assertEqual(r.status_code,200,r.text)
            self.assertIn('checkout.stripe.com',r.json()['checkout_url'])

    def test_order_retrieval_verifies_paid_status_and_product(self):
        session='cs_test_abcde123456789012345'
        with patch.object(server,'stripe',return_value={'metadata':{'optv_product':server.PRODUCT_KEY},'payment_status':'unpaid','amount_total':2999}):
            self.assertEqual(self.client.get('/api/order/'+session).json()['status'],'unpaid')
        with patch.object(server,'stripe',return_value={'metadata':{'optv_product':server.PRODUCT_KEY},'payment_status':'paid','amount_total':2999}):
            self.assertEqual(self.client.get('/api/order/'+session).json()['status'],'paid')
        with patch.object(server,'stripe',return_value={'metadata':{'optv_product':'another_product'},'payment_status':'paid'}):
            self.assertEqual(self.client.get('/api/order/'+session).status_code,404)

    def test_owner_cannot_mark_unpaid_delivered(self):
        session='cs_test_abcde123456789012345'
        with patch.object(server,'stripe',return_value={'metadata':{'optv_product':server.PRODUCT_KEY},'payment_status':'unpaid'}):
            r=self.client.post('/api/admin/delivered',json={'session_id':session},headers={'Authorization':'Bearer admin-private-test-token'})
            self.assertEqual(r.status_code,409)

    def test_owner_marks_paid_order_in_stripe(self):
        session='cs_test_abcde123456789012345'; operations=[]
        def fake(method,path,fields=None,**kwargs):
            operations.append((method,path,fields))
            return {'metadata':{'optv_product':server.PRODUCT_KEY},'payment_status':'paid'}
        with patch.object(server,'stripe',side_effect=fake):
            r=self.client.post('/api/admin/delivered',json={'session_id':session},headers={'Authorization':'Bearer admin-private-test-token'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(any(x[1].startswith('/checkout/sessions/') and x[0]=='POST' for x in operations))
        self.assertFalse(r.json()['sent_by_site'])

    def test_web_security_headers(self):
        r=self.client.get('/')
        self.assertIn('Content-Security-Policy',r.headers)
        self.assertEqual(r.headers['X-Frame-Options'],'DENY')
        self.assertEqual(self.client.post('/api/preview',content=b'x'*9000,headers={'Content-Type':'application/json'}).status_code,413)


if __name__=='__main__':
    unittest.main()

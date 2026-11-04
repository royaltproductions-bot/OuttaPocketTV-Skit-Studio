"""The merch feature cannot collect money before vendor products exist."""
import os
import unittest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
import server

class MerchTests(unittest.TestCase):
    def setUp(self): self.client=TestClient(server.app)

    def test_unconnected_store_is_explicitly_preview(self):
        with patch.dict(os.environ, {'FOURTHWALL_STOREFRONT_TOKEN':'','FOURTHWALL_SHOP_DOMAIN':''}):
            self.assertEqual(self.client.get('/api/merch').json(), {'mode':'preview','products':[]})
        home=self.client.get('/').text
        self.assertIn('id="merch"',home)
        self.assertIn('PREVIEW ONLY · NOT YET FOR SALE',home)
        self.assertIn('/static/merch.js',home)

    @patch('server.requests.get')
    def test_live_published_products_and_token_not_exposed(self, get):
        get.return_value=Mock(status_code=200)
        get.return_value.json.return_value={'results':[
            {'type':'PRODUCT','slug':'not-guilty-mug','name':'Not Guilty Mug',
             'state':{'type':'AVAILABLE'},'images':[{'url':'https://example.com/mug.jpg'}],
             'variants':[{'unitPrice':{'value':17.99,'currency':'USD'}}]},
            {'type':'PRODUCT','slug':'sold-out-shirt','name':'Sold Out Shirt',
             'state':{'type':'SOLD_OUT'},'variants':[]},
            {'type':'BUNDLE','slug':'bundle','name':'Bundle'}
        ]}
        with patch.dict(os.environ, {'FOURTHWALL_STOREFRONT_TOKEN':'ptkn_SECRETVALUE','FOURTHWALL_SHOP_DOMAIN':'optv.fourthwall.com'}):
            response=self.client.get('/api/merch')
        data=response.json()
        self.assertEqual(data['mode'],'live')
        self.assertEqual(len(data['products']),2)
        self.assertEqual(data['products'][0]['price'],17.99)
        self.assertEqual(data['products'][0]['url'],'https://optv.fourthwall.com/products/not-guilty-mug')
        self.assertFalse(data['products'][1]['available'])
        self.assertNotIn('ptkn_SECRETVALUE',response.text)

    @patch('server.requests.get')
    def test_invalid_shop_domain_blocks_network(self, get):
        with patch.dict(os.environ, {'FOURTHWALL_STOREFRONT_TOKEN':'token','FOURTHWALL_SHOP_DOMAIN':'bad.example.com'}):
            self.assertEqual(self.client.get('/api/merch').json()['mode'],'preview')
        get.assert_not_called()

    @patch('server.requests.get')
    def test_vendor_failure_does_not_create_fake_products(self,get):
        get.side_effect=server.requests.RequestException('offline')
        with patch.dict(os.environ, {'FOURTHWALL_STOREFRONT_TOKEN':'token','FOURTHWALL_SHOP_DOMAIN':'optv.fourthwall.com'}):
            self.assertEqual(self.client.get('/api/merch').json()['products'],[])

if __name__=='__main__':unittest.main()

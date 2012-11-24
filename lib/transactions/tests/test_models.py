from mock import patch
from nose.tools import eq_

from lib.sellers.models import Seller, SellerPaypal
from lib.transactions import constants
from lib.transactions.models import Transaction
from solitude.base import APITest


class TestTransaction(APITest):

    def setUp(self):
        self.api_name = 'paypal'
        self.uuid = 'sample:uid'
        self.pay_url = self.get_list_url('pay')
        self.check_url = self.get_list_url('pay-check')
        self.seller = Seller.objects.create(uuid=self.uuid)
        SellerPaypal.objects.create(seller=self.seller,
                                    paypal_id='foo@bar.com')

    def get_data(self):
        return {'amount': '5',
                'currency': 'USD',
                'return_url': 'http://foo.com/return.url',
                'ipn_url': 'http://foo.com/ipn.url',
                'cancel_url': 'http://foo.com/cancel.url',
                'memo': 'Some memo',
                'seller': self.uuid}

    @patch('lib.paypal.client.Client.get_pay_key')
    def test_pay(self, key):
        key.return_value = {'pay_key': 'foo', 'status': 'CREATED',
                            'correlation_id': '123', 'uuid': '456'}
        res = self.client.post(self.pay_url, data=self.get_data())
        eq_(res.status_code, 201)
        qs = Transaction.objects.all()
        eq_(qs.count(), 1)

        obj = qs[0]
        eq_(obj.amount, 5)
        eq_(obj.uid_support, '123')
        eq_(obj.uuid, '456')
        eq_(obj.seller, self.seller)
        eq_(obj.status, constants.STATUS_PENDING)

    @patch('lib.paypal.client.Client.get_pay_key')
    def test_pay_source(self, key):
        key.return_value = {'pay_key': 'foo', 'status': 'CREATED',
                            'correlation_id': '123', 'uuid': '456'}
        data = self.get_data()
        data['source'] = 'in-app'
        res = self.client.post(self.pay_url, data=data)
        eq_(res.status_code, 201)
        eq_(Transaction.objects.all()[0].source, 'in-app')

    @patch('lib.paypal.client.Client.check_purchase')
    def test_checked(self, check):
        check.return_value = {'status': 'COMPLETED', 'pay_key': 'foo'}
        pp = Transaction.objects.create(uid_pay='foo', amount=5,
                                        provider=constants.SOURCE_PAYPAL,
                                        seller=self.seller)
        res = self.client.post(self.check_url, data={'pay_key': 'foo'})
        eq_(res.status_code, 201)
        eq_(Transaction.objects.get(pk=pp.pk).status,
            constants.STATUS_CHECKED)

    @patch('lib.paypal.client.Client.check_purchase')
    def test_complete(self, check):
        check.return_value = {'status': 'COMPLETED', 'pay_key': 'foo'}
        pp = Transaction.objects.create(uid_pay='foo', amount=5,
                                        provider=constants.SOURCE_PAYPAL,
                                        seller=self.seller)
        self.client.post(self.check_url, data={'pay_key': 'foo'})
        eq_(Transaction.objects.get(pk=pp.pk).status,
            constants.STATUS_CHECKED)

        pp.status = constants.STATUS_COMPLETED
        pp.save()
        self.client.post(self.check_url, data={'pay_key': 'foo'})
        eq_(Transaction.objects.get(pk=pp.pk).status,
            constants.STATUS_COMPLETED)

    @patch('lib.paypal.client.Client.check_purchase')
    def test_complete_not_there(self, check):
        check.return_value = {'status': 'COMPLETED', 'pay_key': 'foo'}
        Transaction.objects.create(uid_pay='bar', amount=5,
                                   provider=constants.SOURCE_PAYPAL,
                                   seller=self.seller)
        res = self.client.post(self.check_url, data={'pay_key': 'foo'})
        eq_(res.status_code, 404)

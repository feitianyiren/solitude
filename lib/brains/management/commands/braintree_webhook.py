import base64
import os
import uuid
from datetime import datetime, timedelta
from optparse import make_option

from django.core.management.base import BaseCommand, CommandError

import braintree
import requests
from braintree.util.crypto import Crypto
from payments_config import products

from lib.brains.management.commands.samples import webhooks
from lib.brains.models import BraintreeSubscription
from lib.transactions.models import Transaction
from solitude.logger import getLogger

log = getLogger('s.brains.management')
valid_kinds = [
    'subscription_charged_successfully',
    'subscription_charged_unsuccessfully',
    'subscription_canceled',
]


class Command(BaseCommand):

    """
    This is a crude way to generate and test webhook notifications.

    It does this by grabbing a sample request from Braintree and then
    reformatting it with some local data. There are some inherent problems
    with this:
    * the XML format might change
    * the data is not complete, I've changed the bits I care about, make
      sure the data is set correctly before you rely on it
    * this code will probably go away once we've got decent end to end
      testing in place

    If that doesn't deter you...

        braintree_webhook
                --parse=subscription_charged_successfully
                --subscription_id=14

    Will send this fake and hacky Braintree webhook to that end point.
    You'll need to get subscription_id by looking at the solitude table
    `braintree_subscription`.
    """

    help = ('Generates fakes webhooks sent by braintree for '
            'testing and development. Caveat emptor.')
    option_list = BaseCommand.option_list + (
        make_option(
            '--verify',
            dest='verify',
            action='store_true',
            help=('Send the verify request.')
        ),
        make_option(
            '--parse',
            dest='parse',
            help=('Kind of message to send.')
        ),
        make_option(
            '--server',
            dest='server',
            action='store',
            default='http://pay.dev:8000/api/braintree/webhook/',
            help=('URL of payments-service server.')
        ),
        make_option(
            '--subscription_id',
            default='latest',
            dest='subscription_id',
            help=('Primary key of the subscription in solitude. '
                  'As a special case, set this to `latest` to get the most '
                  'recent one')
        ),
        make_option(
            '--transaction_id',
            dest='transaction_id',
            default=None,
            help=('Primary key of the transaction in solitude (optional). '
                  'As a special case, set this to `latest` to get the most '
                  'recent one')
        )
    )

    def verify(self, url):
        res = requests.get(url, params={'bt_challenge': 'something'})
        res.raise_for_status()
        # Ideally we could do more to verify the challenge is correct.
        if res.status_code != 200:
            raise CommandError(
                'Server did not return a 200 response, got: {}'
                .format(res.status_code)
            )

    def webhook(self, url, kind, sub, trans):
        trans_id = trans.uid_support if trans else str(uuid.uuid4())
        transaction = {
            'subscription_charged_successfully': {
                'id': trans_id,
                'status': 'settled'
            },
            'subscription_charged_unsuccessfully': {
                'id': trans_id,
                'status': 'processor_declined'
            }
        }
        processor_response = {
            'subscription_charged_successfully': {
                'text': 'Approved',
                'code': '1000'
            },
            'subscription_charged_unsuccessfully': {
                'text': 'Invalid Secure Payment Data',
                'code': '2078'
            }
        }

        data = {
            'kind': kind,
            'merchant_account_id': self.merchant,
            'sub': sub,
            'plan_id': sub.seller_product.public_id,
            'product': products[sub.seller_product.public_id],
            'now': datetime.today(),
            'timestamp': datetime.today().strftime('%Y-%m-%dT%H:%M:%SZ'),
            # Braintree doesn't assume months are 30 days long.
            'paid': datetime.today() + timedelta(days=29),
            'next': datetime.today() + timedelta(days=30),
            'transaction': transaction.get(kind),
            'processor_response': processor_response.get(kind)
        }

        xml_blob = webhooks.sub if data['transaction'] else webhooks.no_trans
        xml_formatted = xml_blob.format(**data)
        payload = base64.encodestring(xml_formatted)
        res = requests.post(url, data={
            'bt_signature':
                self.public + '|' +
                Crypto.sha1_hmac_hash(self.private, payload),
            'bt_payload': payload
        })

        res.raise_for_status()
        if res.status_code != 200:
            raise CommandError(
                'Server did not return a 200 response, got: {}'
                .format(res.status_code)
            )

    def handle(self, *args, **options):
        # These environment variables are populated by Docker.
        # This script will not run out of Docker unless you
        # populate these variables.
        self.merchant = os.environ['BRAINTREE_MERCHANT_ID']
        self.public = os.environ['SOLITUDE_AUTH_ENV_BRAINTREE_PUBLIC_KEY']
        self.private = os.environ['SOLITUDE_AUTH_ENV_BRAINTREE_PRIVATE_KEY']

        braintree.Configuration.configure(
            'sandbox', self.merchant, self.public, self.private)

        if options['verify']:
            log.info('Sending verify command')
            return self.verify(options['server'])

        if options['parse']:
            if options['subscription_id'] == 'latest':
                subscription = (BraintreeSubscription.objects
                                .latest('created'))
            else:
                subscription = BraintreeSubscription.objects.get(
                    id=options['subscription_id'])
            transaction = None
            if options['transaction_id']:
                if options['transaction_id'] == 'latest':
                    transaction = Transaction.objects.latest('created')
                else:
                    transaction = Transaction.objects.get(
                        id=options['transaction_id']
                    )
            if options['parse'] not in valid_kinds:
                raise CommandError(
                    'Not a valid kind of webhook: {} must be one of: {}'
                    .format(options['parse'], ', '.join(valid_kinds))
                )

            log.info('Sending parse command for subscription: {}'
                     .format(subscription.id))
            return self.webhook(
                options['server'],
                options['parse'],
                subscription,
                transaction
            )

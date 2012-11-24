from django.db import models
from django.dispatch import receiver

import commonware.log

from lib.bango.signals import create as bango_create
from lib.paypal.signals import create as paypal_create
from lib.transactions import constants

from solitude.base import get_object_or_404, Model

log = commonware.log.getLogger('s.transaction')


class Transaction(Model):
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    buyer = models.ForeignKey('buyers.Buyer', blank=True, null=True,
                              db_index=True)
    currency = models.CharField(max_length=3, default='USD')
    provider = models.PositiveIntegerField(
                              choices=sorted(constants.SOURCES.items()))
    related = models.ForeignKey('self', blank=True, null=True,
                              on_delete=models.PROTECT)
    seller = models.ForeignKey('sellers.Seller', db_index=True)
    status = models.PositiveIntegerField(default=constants.STATUS_DEFAULT,
                              choices=sorted(constants.STATUSES.items()))
    source = models.CharField(max_length=255, blank=True, null=True,
                              db_index=True)
    type = models.PositiveIntegerField(default=constants.TYPE_DEFAULT,
                              choices=sorted(constants.TYPES.items()))
    uid_support = models.CharField(max_length=255, db_index=True, unique=True)
    uid_pay = models.CharField(max_length=255, db_index=True, unique=True)
    uuid = models.CharField(max_length=255, db_index=True, unique=True)

    class Meta(Model.Meta):
        db_table = 'transaction'
        ordering = ('-id',)


@receiver(paypal_create, dispatch_uid='transaction-create-paypal')
def create_paypal_transaction(sender, **kwargs):
    if sender.__class__._meta.resource_name != 'pay':
        return

    data = kwargs['bundle'].data
    clean = kwargs['form']

    transaction = Transaction.objects.create(
            amount=clean['amount'],
            currency=clean['currency'],
            provider=constants.SOURCE_PAYPAL,
            seller=clean['seller'],
            source=clean.get('source', ''),
            type=constants.TYPE_PAYMENT,
            uid_pay=data['pay_key'],
            uid_support=data['correlation_id'],
            uuid=data['uuid'])
    log.info('Transaction: %s, paypal status: %s'
             % (transaction.pk, data['status']))


@receiver(paypal_create, dispatch_uid='transaction-complete-paypal')
def completed_paypal_transaction(sender, **kwargs):
    if sender.__class__._meta.resource_name != 'pay-check':
        return

    data = kwargs['bundle'].data
    transaction = get_object_or_404(Transaction, uid_pay=data['pay_key'])

    if transaction.status == constants.STATUS_PENDING:
        log.info('Transaction: %s, paypal status: %s'
                 % (transaction.pk, data['status']))
        if data['status'] == 'COMPLETED':
            transaction.status = constants.STATUS_CHECKED
            transaction.save()


@receiver(bango_create, dispatch_uid='transaction-create-bango')
def create_bango_transaction(sender, **kwargs):
    if sender.__class__._meta.resource_name != 'create-billing':
        return

    # Pull information from all the over the place.
    bundle = kwargs['bundle'].data
    data = kwargs['data']
    form = kwargs['form']
    seller = form.cleaned_data['seller_product_bango'].seller_bango.seller

    transaction = Transaction.objects.create(
            amount=form.cleaned_data['price_amount'],
            provider=constants.SOURCE_BANGO,
            seller=seller,
            source=data.get('source', ''),
            type=constants.TYPE_PAYMENT,
            uuid=data['externalTransactionId'],
            uid_pay=bundle['billingConfigurationId'])

    log.info('Bango transaction: %s pending' % (transaction.pk,))

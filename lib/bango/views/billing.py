from django.conf import settings

from rest_framework.decorators import api_view
from rest_framework.response import Response

from lib.bango.client import BangoError, get_client
from lib.bango.constants import MICRO_PAYMENT_TYPES, PAYMENT_TYPES
from lib.bango.errors import ProcessError
from lib.bango.forms import CreateBillingConfigurationForm
from lib.bango.serializers import SellerProductBangoOnly
from lib.bango.utils import sign
from lib.bango.views.base import BangoResource
from solitude.constants import PAYMENT_METHOD_OPERATOR
from solitude.logger import getLogger

log = getLogger('s.bango')


def prepare(form, bango):
    data = form.bango_data
    # Add in the Bango number from the serializer.
    data['bango'] = bango

    # Used to create the approprate data structure.
    client = get_client()
    billing = client.client('billing')
    price_list = billing.factory.create('ArrayOfPrice')
    price_types = set()

    for item in form.cleaned_data['prices']:
        price = billing.factory.create('Price')
        price.amount = item.cleaned_data['price']
        price.currency = item.cleaned_data['currency']
        price_types.add(item.cleaned_data['method'])

        # TODO: remove this.
        # Very temporary and very fragile hack to fix bug 882183.
        # Bango cannot accept regions with price info so if there
        # are two USD values for different regions it triggers a 500 error.
        append = True
        for existing in price_list.Price:
            if existing.currency == price.currency:
                log.info('Skipping %s:%s because we already have %s:%s'
                         % (price.currency, price.amount,
                            existing.currency, existing.amount))
                append = False
                break

        if append:
            price_list.Price.append(price)

    data['priceList'] = price_list

    # More workarounds for bug 882321, ideally we'd send one type per
    # region, price, combination. If all the prices say operator, then
    # we'll set it to that. Otherwise its all.
    type_filters = PAYMENT_TYPES
    if price_types == set([str(PAYMENT_METHOD_OPERATOR)]):
        type_filters = MICRO_PAYMENT_TYPES

    types = billing.factory.create('ArrayOfString')
    for f in type_filters:
        types.string.append(f)
    data['typeFilter'] = types

    config = billing.factory.create('ArrayOfBillingConfigurationOption')
    configs = {
        'APPLICATION_CATEGORY_ID': '18',
        'APPLICATION_SIZE_KB': data.pop('application_size'),
        # Tell Bango to use our same transaction expiry logic.
        # However, we pad it by 60 seconds to show a prettier Mozilla user
        # error in the case of a real timeout.
        'BILLING_CONFIGURATION_TIME_OUT': settings.TRANSACTION_EXPIRY + 60,
        'REDIRECT_URL_ONSUCCESS': data.pop('redirect_url_onsuccess'),
        'REDIRECT_URL_ONERROR': data.pop('redirect_url_onerror'),
        'REQUEST_SIGNATURE': sign(data['externalTransactionId']),
    }
    user_uuid = data.pop('user_uuid')
    if settings.SEND_USER_ID_TO_BANGO:
        configs['MOZ_USER_ID'] = user_uuid
        log.info('Sending MOZ_USER_ID {uuid} for transaction {tr}'
                 .format(uuid=user_uuid, tr=data['externalTransactionId']))
    if settings.BANGO_ICON_URLS:
        icon_url = data.pop('icon_url', None)
        if icon_url:
            configs['APPLICATION_LOGO_URL'] = icon_url

    for k, v in configs.items():
        opt = billing.factory.create('BillingConfigurationOption')
        opt.configurationOptionName = k
        opt.configurationOptionValue = v
        config.BillingConfigurationOption.append(opt)

    data['configurationOptions'] = config
    return data


@api_view(['POST'])
def billing(request):
    """
    Call the Bango API to begin a payment transaction.

    The resulting billingConfigId can be used on the query
    string in a URL to initiate a user payment flow.

    We are able to configure a few parameters that come
    back to us on the Bango success URL query string.
    Here are some highlights:

    **config[REQUEST_SIGNATURE]**
        This arrives as **MozSignature** in the redirect query string.

    **externalTransactionId**
        This is set to solitude's own transaction_uuid. It arrives
        in the redirect query string as **MerchantTransactionId**.
    """

    view = BangoResource()
    try:
        serial, form = view.process(
            serial_class=SellerProductBangoOnly,
            form_class=CreateBillingConfigurationForm,
            request=request)
    except ProcessError, exc:
        return exc.response

    transaction_uuid = form.bango_data['externalTransactionId']
    bango = serial.object['seller_product_bango'].bango_id

    try:
        data = prepare(form, bango)
        resp = view.client('CreateBillingConfiguration', data)
    except BangoError:
        log.error('Error on createBillingConfiguration, uuid: {0}'
                  .format(transaction_uuid))
        raise

    response_data = {
        'responseCode': resp.responseCode,
        'responseMessage': resp.responseMessage,
        'billingConfigurationId': resp.billingConfigurationId
    }

    log.info('Sending trans uuid %s from Bango config %s'
             % (transaction_uuid,
                response_data['billingConfigurationId']))
    return Response(response_data)

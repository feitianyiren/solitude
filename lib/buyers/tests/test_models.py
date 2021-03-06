from datetime import datetime, timedelta

from django.conf import settings
from django.dispatch import receiver
from django.test import TestCase

from aesfield.field import EncryptedField
from nose.tools import eq_

from lib.buyers.models import ANONYMISED, Buyer


class TestEncryption(TestCase):
    # This is mostly because of a lack of tests in aesfield.
    # Let's just check this all works as we'd expect.

    def test_add_something(self):
        bp = Buyer.objects.create(email='f@f.c')
        eq_(Buyer.objects.get(pk=bp.pk).email, 'f@f.c')

    def test_update_something(self):
        bp = Buyer.objects.create(email='f@f.c')
        bp.email = 'b@b.c'
        bp.save()
        eq_(Buyer.objects.get(pk=bp.pk).email, 'b@b.c')

    def test_set_empty(self):
        bp = Buyer.objects.create(email='f@f.c')
        bp.email = ''
        bp.save()
        eq_(Buyer.objects.get(pk=bp.pk).email, '')

    def test_filter(self):
        with self.assertRaises(EncryptedField):
            Buyer.objects.filter(email='f@f.c')

    def test_email_sig(self):
        obj = Buyer.objects.create(email='f@f.c')
        assert str(obj.email_sig).startswith('consistent:')

    def test_email_sig_consistent(self):
        obj = Buyer.objects.create(email='consistent:f')
        assert str(obj.email_sig) != 'consistent:f'


class TestLockout(TestCase):

    def setUp(self):
        self.uid = 'test:uid'
        self.buyer = Buyer.objects.create(uuid=self.uid)

    def test_locked_out(self):
        assert not self.buyer.locked_out
        self.buyer.pin_locked_out = datetime.now()
        self.buyer.save()
        assert self.buyer.reget().locked_out

    def test_increment(self):
        for x in range(1, settings.PIN_FAILURES + 1):
            res = self.buyer.incr_lockout()
            buyer = self.buyer.reget()
            eq_(buyer.pin_failures, x)

            # On the last pass, we should be locked out.
            if x == settings.PIN_FAILURES:
                assert res
                assert buyer.pin_locked_out
                assert buyer.pin_was_locked_out
            else:
                assert not res
                assert not buyer.pin_locked_out

    def test_clear(self):
        self.buyer.pin_failues = 1
        self.buyer.pin_locked_out = datetime.now()
        self.buyer.clear_lockout()
        eq_(self.buyer.pin_failures, 0)
        eq_(self.buyer.pin_locked_out, None)

    def test_was_locked_out(self):
        self.buyer.pin_failures = settings.PIN_FAILURES
        self.buyer.save()
        self.buyer.incr_lockout()
        self.buyer = self.buyer.reget()
        assert self.buyer.pin_was_locked_out
        self.buyer.clear_lockout()
        self.buyer = self.buyer.reget()
        assert self.buyer.pin_was_locked_out

    def test_clear_was_locked_out(self):
        self.buyer.pin_failures = settings.PIN_FAILURES
        self.buyer.save()
        self.buyer.incr_lockout()
        self.buyer = self.buyer.reget()
        assert self.buyer.pin_was_locked_out
        self.buyer.clear_lockout(clear_was_locked=True)
        self.buyer = self.buyer.reget()
        assert not self.buyer.pin_was_locked_out

    def test_under_timeout(self):
        self.buyer.pin_locked_out = (
            datetime.now() -
            timedelta(seconds=settings.PIN_FAILURE_LENGTH - 60))
        self.buyer.save()
        assert self.buyer.locked_out

    def test_over_timeout(self):
        self.buyer.pin_locked_out = (
            datetime.now() -
            timedelta(seconds=settings.PIN_FAILURE_LENGTH + 60))
        self.buyer.save()
        assert not self.buyer.locked_out
        eq_(self.buyer.reget().pin_locked_out, None)


class TestClose(TestCase):

    def setUp(self):
        self.uid = 'some:buyer'
        self.buyer = Buyer.objects.create(
            email='f@b.com', uuid=self.uid)

    def test_close(self):
        self.buyer.close()
        buyer = self.buyer.reget()
        eq_(buyer.active, False)
        eq_(buyer.email, '')
        eq_(str(buyer.email_sig), '')
        assert buyer.uuid.startswith(ANONYMISED)

    def test_repeat(self):
        self.buyer.close()
        with self.assertRaises(ValueError):
            self.buyer.close()

    def test_signal(self):
        self.called = True

        @receiver(self.buyer.close_signal, sender=self.buyer.__class__)
        def signal(sender, *args, **kw):
            eq_(kw['buyer'], self.buyer)
            self.called = True

        self.buyer.close()
        assert self.called

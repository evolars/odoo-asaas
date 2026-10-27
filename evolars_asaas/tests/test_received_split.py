from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.asaas_base.models.asaas_client import AsaasClient

ORIGIN = "91384bb1-398e-462c-8a01-7d914ccfcc76"


def _split(split_id, status="DONE", value=13.54, credit_date="2026-10-26", origin=ORIGIN):
    return {
        "id": split_id,
        "walletId": "wal_evolars",
        "fixedValue": None,
        "percentualValue": 5.0,
        "totalValue": value,
        "status": status,
        "originAccountId": origin,
        "creditDate": credit_date if status != "PENDING" else None,
        "payment": {
            "id": "pay_%s" % split_id,
            "confirmedDate": credit_date,
            "invoiceNumber": "946750135",
            "externalReference": "rodrigoreis:50.00",
        },
    }


@tagged("post_install", "-at_install")
class TestReceivedSplit(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Parceiro do Split",
            "asaas_account_id": ORIGIN,
        })
        cls.journal = cls.env["account.journal"].create({
            "name": "Asaas Split Teste",
            "code": "ASSPL",
            "type": "bank",
        })
        params = cls.env["ir.config_parameter"].sudo()
        params.set_param("evolars_asaas.receivable_journal_id", cls.journal.id)
        params.set_param("asaas.api_key", "aact_test")
        Move = cls.env["account.move"]

        def sync(splits, **kwargs):
            page = {"data": splits, "hasMore": False}
            with patch.object(AsaasClient, "request", return_value=page) as request:
                remaining = Move.asaas_sync_received_splits(**kwargs)
            return remaining, request

        cls.sync = staticmethod(sync)

    def _invoices(self):
        return self.env["account.move"].search([("partner_id", "=", self.partner.id), ("move_type", "=", "out_invoice")])

    def test_done_split_becomes_paid_invoice(self):
        self.sync([_split("s1")])
        invoice = self._invoices()
        self.assertEqual(len(invoice), 1)
        self.assertEqual(invoice.state, "posted")
        self.assertIn(invoice.payment_state, ("paid", "in_payment"))
        self.assertEqual(invoice.amount_total, 13.54)
        self.assertEqual(str(invoice.invoice_date), "2026-10-26")
        self.assertEqual(invoice.asaas_split_id, "s1")
        self.assertFalse(invoice.invoice_line_ids.tax_ids)
        self.assertIn("5%", invoice.invoice_line_ids.name)
        self.assertIn("rodrigoreis:50.00", invoice.invoice_line_ids.name)
        payment = self.env["account.payment"].search([("partner_id", "=", self.partner.id)])
        self.assertEqual(payment.journal_id, self.journal)
        self.assertEqual(str(payment.date), "2026-10-26")

    def test_sync_is_idempotent(self):
        self.sync([_split("s1")])
        self.sync([_split("s1")])
        self.assertEqual(len(self._invoices()), 1)

    def test_pending_cancelled_and_unknown_origin_are_ignored(self):
        self.sync([
            _split("p1", status="PENDING"),
            _split("c1", status="CANCELLED"),
            _split("r1", status="REFUNDED"),
            _split("o1", origin="outra-conta"),
        ])
        self.assertFalse(self._invoices())

    def test_refund_after_invoicing_creates_paid_credit_note(self):
        self.sync([_split("s1")])
        self.sync([_split("s1", status="REFUNDED")])
        invoice = self._invoices()
        refund = invoice.reversal_move_ids
        self.assertEqual(len(refund), 1)
        self.assertEqual(refund.state, "posted")
        self.assertIn(refund.payment_state, ("paid", "in_payment"))
        self.sync([_split("s1", status="REFUNDED")])
        self.assertEqual(len(invoice.reversal_move_ids), 1)

    def test_limit_keeps_cursor_until_backlog_is_done(self):
        params = self.env["ir.config_parameter"].sudo()
        splits = [_split("s1", credit_date="2026-01-09"), _split("s2", credit_date="2026-02-10")]
        remaining, _request = self.sync(splits, limit=1)
        self.assertEqual(remaining, 1)
        self.assertEqual(str(self._invoices().invoice_date), "2026-01-09")
        self.assertFalse(params.get_param("evolars_asaas.received_split_since"))
        remaining, _request = self.sync(splits, limit=1)
        self.assertEqual(remaining, 0)
        self.assertEqual(params.get_param("evolars_asaas.received_split_since"), "2026-02-10")
        _remaining, request = self.sync([])
        self.assertEqual(request.call_args.kwargs["params"]["creditDate[ge]"], "2026-02-03")

    def test_without_mapped_partner_does_not_call_asaas(self):
        self.partner.asaas_account_id = False
        _remaining, request = self.sync([_split("s1")])
        request.assert_not_called()

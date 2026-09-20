import json
import sys
import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import types
from pathlib import Path

if "odoo" not in sys.modules:
    odoo_mock = types.ModuleType("odoo")
    odoo_mock.fields = MagicMock()
    odoo_mock.fields.Date.today.return_value = date(2026, 9, 9)
    odoo_mock.fields.Datetime.now.return_value = datetime(2026, 9, 9, 12, 0, 0)
    odoo_mock._ = lambda msg: msg
    odoo_exceptions = types.ModuleType("odoo.exceptions")
    class UserError(Exception): pass
    class ValidationError(Exception): pass
    odoo_exceptions.UserError = UserError
    odoo_exceptions.ValidationError = ValidationError
    sys.modules["odoo"] = odoo_mock
    sys.modules["odoo.exceptions"] = odoo_exceptions

import importlib.util
spec = importlib.util.spec_from_file_location(
    "notification_service",
    Path(__file__).resolve().parent.parent / "models" / "notification_service.py"
)
notif_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notif_mod)

EvolarsNotificationService = notif_mod.EvolarsNotificationService
FROM_EMAIL = notif_mod.FROM_EMAIL



class TestNotificationChannels(unittest.TestCase):
    def setUp(self):
        self.mock_params = {
            "evolars_lydia.evolution_api_url": "https://evolution.evolars.com.br",
            "evolars_lydia.evolution_instance": "lydia",
            "evolars_lydia.evolution_api_key": "test_evo_key_xyz",
            "evolars_asaas.resend_api_key": "re_test_key_123",
        }
        self.env = MagicMock()
        self.env["ir.config_parameter"].sudo().get_param.side_effect = (
            lambda key, default=None: self.mock_params.get(key, default)
        )
        self.service = EvolarsNotificationService(self.env)

        self.partner = MagicMock()
        self.partner.id = 42
        self.partner.name = "Jerimarkas SA"
        self.partner.email = "contato@jerimarkas.com"
        self.partner.mobile = "48991234567"
        self.partner.phone = "4832345678"
        self.partner.vat = "12.345.678/0001-90"
        self.partner.notification_channel = "both"

        self.company = MagicMock()
        self.company.id = 1
        self.company.name = "Evolars LTDA"

    def test_format_phone_number(self):
        self.assertEqual(self.service.format_phone_number("(48) 99123-4567"), "5548991234567")
        self.assertEqual(self.service.format_phone_number("48991234567"), "5548991234567")
        self.assertEqual(self.service.format_phone_number("+55 48 99123-4567"), "5548991234567")
        self.assertEqual(self.service.format_phone_number("5548991234567"), "5548991234567")
        self.assertEqual(self.service.format_phone_number("244923123456"), "244923123456")
        self.assertFalse(self.service.format_phone_number(""))
        self.assertFalse(self.service.format_phone_number("123"))

    def test_evolution_config_retrieval(self):
        config = self.service._get_evolution_config()
        self.assertEqual(config["base_url"], "https://evolution.evolars.com.br")
        self.assertEqual(config["instance"], "lydia")
        self.assertEqual(config["api_key"], "test_evo_key_xyz")

    def test_resend_api_key_retrieval(self):
        key = self.service._get_resend_api_key()
        self.assertEqual(key, "re_test_key_123")

    @patch("urllib.request.urlopen")
    def test_send_whatsapp_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.read.return_value = json.dumps({"key": {"id": "MSG_EVO_001"}}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        # Ensure env does not have evolars.lydia.whatsapp.message for direct REST test
        self.env.__contains__.side_effect = lambda key: key != "evolars.lydia.whatsapp.message"

        res = self.service.send_whatsapp(
            phone_number="48991234567",
            message_body="Teste de cobrança WhatsApp",
            partner=self.partner,
            company=self.company,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["message_id"], "MSG_EVO_001")

    @patch("requests.post")
    def test_send_email_resend_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "re_email_999"}
        mock_post.return_value = mock_resp

        res = self.service.send_email_resend(
            to_email="contato@jerimarkas.com",
            subject="Cobrança Teste",
            html_body="<p>Olá</p>",
            text_body="Olá",
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["id"], "re_email_999")
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["to"], ["contato@jerimarkas.com"])
        self.assertEqual(kwargs["json"]["from"], FROM_EMAIL)

    def test_notify_payment_both_channels(self):
        payment = MagicMock()
        payment.id = 101
        payment.name = "COB/2026/0001"
        payment.partner_id = self.partner
        payment.notification_channel = "both"
        payment.amount = 1500.00
        payment.due_date = date(2026, 9, 20)
        payment.received_date = False
        payment.billing_type = "PIX"
        payment.pix_payload = "00020126580014br.gov.bcb.pix..."
        payment.identification_field = False
        payment.invoice_url = "https://asaas.com/i/test1234"
        payment.company_id = self.company
        payment.invoice_id = MagicMock(name="INV/2026/001")

        with patch.object(self.service, "send_whatsapp", return_value={"success": True, "message_id": "WA_101"}) as mock_wa:
            with patch.object(self.service, "send_email_resend", return_value={"success": True, "id": "MAIL_101"}) as mock_mail:
                results = self.service.notify_payment(payment, event="created")
                self.assertTrue(results["whatsapp"]["success"])
                self.assertTrue(results["email"]["success"])
                mock_wa.assert_called_once()
                mock_mail.assert_called_once()

    def test_notify_payment_whatsapp_only(self):
        payment = MagicMock()
        payment.id = 102
        payment.name = "COB/2026/0002"
        payment.partner_id = self.partner
        payment.notification_channel = "whatsapp"
        payment.amount = 500.00
        payment.due_date = date(2026, 9, 25)
        payment.received_date = False
        payment.billing_type = "BOLETO"
        payment.pix_payload = False
        payment.identification_field = "34191.79001 01043.510047 91020.150008 5 91200000050000"
        payment.invoice_url = "https://asaas.com/b/test1234"
        payment.company_id = self.company
        payment.invoice_id = False

        with patch.object(self.service, "send_whatsapp", return_value={"success": True, "message_id": "WA_102"}) as mock_wa:
            with patch.object(self.service, "send_email_resend") as mock_mail:
                results = self.service.notify_payment(payment, event="created")
                self.assertTrue(results["whatsapp"]["success"])
                self.assertIsNone(results["email"])
                mock_wa.assert_called_once()
                mock_mail.assert_not_called()

    def test_notify_payment_email_only(self):
        payment = MagicMock()
        payment.id = 103
        payment.name = "COB/2026/0003"
        payment.partner_id = self.partner
        payment.notification_channel = "email"
        payment.amount = 2500.00
        payment.due_date = date(2026, 9, 30)
        payment.received_date = False
        payment.billing_type = "PIX"
        payment.pix_payload = "pix_abc_123"
        payment.identification_field = False
        payment.invoice_url = "https://asaas.com/i/test999"
        payment.company_id = self.company
        payment.invoice_id = False

        with patch.object(self.service, "send_whatsapp") as mock_wa:
            with patch.object(self.service, "send_email_resend", return_value={"success": True, "id": "MAIL_103"}) as mock_mail:
                results = self.service.notify_payment(payment, event="created")
                self.assertIsNone(results["whatsapp"])
                self.assertTrue(results["email"]["success"])
                mock_wa.assert_not_called()
                mock_mail.assert_called_once()

    def test_notify_payment_disabled_channel(self):
        payment = MagicMock()
        payment.id = 104
        payment.partner_id = self.partner
        payment.notification_channel = "none"

        with patch.object(self.service, "send_whatsapp") as mock_wa:
            with patch.object(self.service, "send_email_resend") as mock_mail:
                results = self.service.notify_payment(payment, event="created")
                self.assertIsNone(results["whatsapp"])
                self.assertIsNone(results["email"])
                mock_wa.assert_not_called()
                mock_mail.assert_not_called()

    def test_notify_subscription_activation(self):
        subscription = MagicMock()
        subscription.id = 201
        subscription.name = "SaaS Enterprise - Jerimarkas"
        subscription.partner_id = self.partner
        subscription.notification_channel = "both"
        subscription.amount = 3500.00
        subscription.cycle = "MONTHLY"
        subscription.next_due_date = date(2026, 10, 5)
        subscription.billing_type = "PIX"
        subscription.asaas_subscription_id = "sub_xyz_888"
        subscription.company_id = self.company
        subscription.contract_id = MagicMock(name="CONTRATO-2026-005")

        with patch.object(self.service, "send_whatsapp", return_value={"success": True, "message_id": "WA_SUB_201"}) as mock_wa:
            with patch.object(self.service, "send_email_resend", return_value={"success": True, "id": "MAIL_SUB_201"}) as mock_mail:
                results = self.service.notify_subscription(subscription, event="activated")
                self.assertTrue(results["whatsapp"]["success"])
                self.assertTrue(results["email"]["success"])
                mock_wa.assert_called_once()
                mock_mail.assert_called_once()


if __name__ == "__main__":
    unittest.main()

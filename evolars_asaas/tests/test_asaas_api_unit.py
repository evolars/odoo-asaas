import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

if "odoo" not in sys.modules:
    odoo_mock = types.ModuleType("odoo")
    odoo_mock._ = lambda msg: msg
    odoo_exceptions = types.ModuleType("odoo.exceptions")
    class UserError(Exception): pass
    class ValidationError(Exception): pass
    odoo_exceptions.UserError = UserError
    odoo_exceptions.ValidationError = ValidationError
    odoo_mock.exceptions = odoo_exceptions

    odoo_http = types.ModuleType("odoo.http")
    odoo_http.Controller = object
    odoo_http.route = lambda *args, **kwargs: (lambda f: f)
    odoo_http.request = MagicMock()
    odoo_mock.http = odoo_http

    sys.modules["odoo"] = odoo_mock
    sys.modules["odoo.exceptions"] = odoo_exceptions
    sys.modules["odoo.http"] = odoo_http

import importlib.util
spec = importlib.util.spec_from_file_location(
    "asaas_api",
    Path(__file__).resolve().parent.parent / "models" / "asaas_api.py"
)
asaas_api_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(asaas_api_mod)
AsaasAPI = asaas_api_mod.AsaasAPI



class TestAsaasAPIUnit(unittest.TestCase):
    def setUp(self):
        self.mock_params = {
            "evolars_asaas.api_key": "test_api_key_xyz",
            "evolars_asaas.environment": "sandbox",
        }
        self.env = {
            "ir.config_parameter": MagicMock(
                sudo=lambda: MagicMock(
                    get_param=lambda key, default=None: self.mock_params.get(key, default)
                )
            )
        }
        self.api = AsaasAPI(self.env)

    def test_payment_methods(self):
        with patch.object(self.api, "request", return_value={"id": "pay_1"}) as mock_req:
            self.api.get_payment("pay_1")
            mock_req.assert_called_once_with("GET", "/payments/pay_1")

        with patch.object(self.api, "request", return_value={"id": "pay_1"}) as mock_req:
            self.api.update_payment("pay_1", {"value": 100.0})
            mock_req.assert_called_once_with("PUT", "/payments/pay_1", {"value": 100.0})

        with patch.object(self.api, "request", return_value={"deleted": True}) as mock_req:
            self.api.delete_payment("pay_1")
            mock_req.assert_called_once_with("DELETE", "/payments/pay_1")

        with patch.object(self.api, "request", return_value={"id": "ref_1"}) as mock_req:
            self.api.refund_payment("pay_1", value=50.0, description="Refund")
            mock_req.assert_called_once_with("POST", "/payments/pay_1/refund", {"value": 50.0, "description": "Refund"})

        with patch.object(self.api, "request", return_value={"id": "pay_1"}) as mock_req:
            self.api.receive_in_cash("pay_1", "2026-09-08", value=100.0)
            mock_req.assert_called_once_with("POST", "/payments/pay_1/receiveInCash", {"paymentDate": "2026-09-08", "notifyCustomer": False, "value": 100.0})

        with patch.object(self.api, "request", return_value={"payload": "pix..."}) as mock_req:
            self.api.get_pix_qrcode("pay_1")
            mock_req.assert_called_once_with("GET", "/payments/pay_1/pixQrCode")

        with patch.object(self.api, "request", return_value={"identificationField": "123..."}) as mock_req:
            self.api.get_identification_field("pay_1")
            mock_req.assert_called_once_with("GET", "/payments/pay_1/identificationField")

        with patch.object(self.api, "request", return_value={}) as mock_req:
            self.api.resend_payment_notification("pay_1")
            mock_req.assert_called_once_with("POST", "/payments/pay_1/resendPaymentNotification")

    def test_subscription_methods(self):
        with patch.object(self.api, "request", return_value={"id": "sub_1"}) as mock_req:
            self.api.get_subscription("sub_1")
            mock_req.assert_called_once_with("GET", "/subscriptions/sub_1")

        with patch.object(self.api, "request", return_value={"id": "sub_1"}) as mock_req:
            self.api.update_subscription("sub_1", {"value": 200.0})
            mock_req.assert_called_once_with("PUT", "/subscriptions/sub_1", {"value": 200.0})

        with patch.object(self.api, "request", return_value={"deleted": True}) as mock_req:
            self.api.delete_subscription("sub_1")
            mock_req.assert_called_once_with("DELETE", "/subscriptions/sub_1")

        with patch.object(self.api, "request", return_value={"data": []}) as mock_req:
            self.api.get_subscription_payments("sub_1")
            mock_req.assert_called_once_with("GET", "/subscriptions/sub_1/payments")

    def test_customer_methods(self):
        with patch.object(self.api, "request", return_value={"id": "cus_1"}) as mock_req:
            self.api.get_customer("cus_1")
            mock_req.assert_called_once_with("GET", "/customers/cus_1")

        with patch.object(self.api, "request", return_value={"id": "cus_1"}) as mock_req:
            self.api.update_customer("cus_1", {"name": "Novo Nome"})
            mock_req.assert_called_once_with("PUT", "/customers/cus_1", {"name": "Novo Nome"})

    def test_transfer_pix_and_balance(self):
        with patch.object(self.api, "request", return_value={"balance": 2434.06}) as mock_req:
            res = self.api.get_balance()
            mock_req.assert_called_once_with("GET", "/finance/balance")
            self.assertEqual(res["balance"], 2434.06)

        with patch.object(self.api, "request", return_value={"id": "tr_1", "status": "DONE"}) as mock_req:
            res = self.api.transfer_pix(
                value=1200.0,
                pix_address_key="julia@example.com",
                pix_address_key_type="EMAIL",
                description="Remuneracao Julia",
                external_reference="odoo-bill-45"
            )
            mock_req.assert_called_once_with("POST", "/transfers", {
                "value": 1200.0,
                "operationType": "PIX",
                "pixAddressKey": "julia@example.com",
                "pixAddressKeyType": "EMAIL",
                "description": "Remuneracao Julia",
                "externalReference": "odoo-bill-45"
            })
            self.assertEqual(res["status"], "DONE")

        with patch.object(self.api, "request", return_value={"id": "tr_1"}) as mock_req:
            self.api.get_transfer("tr_1")
            mock_req.assert_called_once_with("GET", "/transfers/tr_1")

    def test_missing_api_key_raises_error(self):
        self.api.api_key = False
        with self.assertRaises(UserError):
            self.api.request("GET", "/payments")

    def test_webhook_methods(self):
        with patch.object(self.api, "request", return_value={"data": []}) as mock_req:
            self.api.get_webhooks()
            mock_req.assert_called_once_with("GET", "/webhooks")

        with patch.object(self.api, "request", return_value={"id": "wh_1"}) as mock_req:
            self.api.get_webhook("wh_1")
            mock_req.assert_called_once_with("GET", "/webhooks/wh_1")

        with patch.object(self.api, "request", return_value={"id": "wh_1"}) as mock_req:
            self.api.create_webhook({"name": "Test"})
            mock_req.assert_called_once_with("POST", "/webhooks", {"name": "Test"})

        with patch.object(self.api, "request", return_value={"id": "wh_1"}) as mock_req:
            self.api.update_webhook("wh_1", {"enabled": True})
            mock_req.assert_called_once_with("PUT", "/webhooks/wh_1", {"enabled": True})

    def test_sync_webhook_updates_existing(self):
        existing_data = {
            "data": [
                {"id": "wh_existing", "name": "Odoo Evolars", "url": "https://evolars.com.br/evolars_asaas/webhook"}
            ]
        }
        with patch.object(self.api, "get_webhooks", return_value=existing_data), \
             patch.object(self.api, "update_webhook", return_value={"id": "wh_existing", "interrupted": False}) as mock_update:
            res = self.api.sync_webhook(
                target_url="https://evolars.com.br/evolars_asaas/webhook",
                auth_token="test_token_123",
                email="admin@evolars.com.br"
            )
            mock_update.assert_called_once()
            args = mock_update.call_args[0]
            self.assertEqual(args[0], "wh_existing")
            self.assertEqual(args[1]["authToken"], "test_token_123")
            self.assertFalse(args[1]["interrupted"])
            self.assertIn("PAYMENT_CHECKOUT_VIEWED", args[1]["events"])
            self.assertIn("PAYMENT_OVERDUE", args[1]["events"])
            self.assertIn("BILL_PAID", args[1]["events"])


if __name__ == "__main__":
    unittest.main()


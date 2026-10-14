import json

from odoo.tests.common import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestWithdrawalValidation(HttpCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("asaas.withdrawal_token", "tok_saque_123")

    def _post(self, payload, token="tok_saque_123"):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["asaas-access-token"] = token
        return self.url_open("/asaas/withdrawal", data=json.dumps(payload), headers=headers)

    def test_wrong_or_missing_token_is_forbidden(self):
        self.assertEqual(self._post({"type": "TRANSFER"}, token="errado").status_code, 403)
        self.assertEqual(self._post({"type": "TRANSFER"}, token=None).status_code, 403)
        self.assertFalse(self.env["asaas.withdrawal.validation"].search([]))

    def test_unknown_operation_is_refused_and_recorded(self):
        response = self._post({"type": "TRANSFER", "transfer": {"id": "tr_x", "value": 10.0}})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "REFUSED")
        self.assertTrue(body["refuseReason"])
        record = self.env["asaas.withdrawal.validation"].search([("asaas_id", "=", "tr_x")])
        self.assertEqual(record.decision, "refused")
        self.assertEqual(record.value, 10.0)

    def test_no_token_configured_refuses_everything(self):
        self.env["ir.config_parameter"].sudo().set_param("asaas.withdrawal_token", "")
        self.assertEqual(self._post({"type": "TRANSFER"}, token="").status_code, 403)

import json
from unittest.mock import patch

import requests

from odoo.tests import TransactionCase, tagged

from odoo.addons.asaas_base.models.asaas_client import (
    PRODUCTION_URL,
    SANDBOX_URL,
    USER_AGENT,
    AsaasClient,
    AsaasError,
    customer_payload,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, reason="OK"):
        self.status_code = status_code
        self.reason = reason
        self._payload = payload if payload is not None else {}
        self.content = json.dumps(self._payload).encode()

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


@tagged("post_install", "-at_install", "asaas_base")
class TestAsaasClient(TransactionCase):

    def test_sandbox_and_production_urls(self):
        self.assertEqual(AsaasClient("k", sandbox=True).base_url, SANDBOX_URL)
        self.assertEqual(AsaasClient("k", sandbox=False).base_url, PRODUCTION_URL)

    def test_request_sends_credentials_and_user_agent(self):
        """O Asaas recusa contas novas sem User-Agent — o header não é opcional."""
        client = AsaasClient("$aact_hmlg_abc", sandbox=True)
        with patch.object(requests, "request", return_value=FakeResponse(payload={"id": "cus_1"})) as call:
            client.create_customer({"name": "Fulano"})

        _, kwargs = call.call_args[0], call.call_args[1]
        self.assertEqual(kwargs["headers"]["access_token"], "$aact_hmlg_abc")
        self.assertEqual(kwargs["headers"]["User-Agent"], USER_AGENT)
        self.assertEqual(call.call_args[0][1], "%s/customers" % SANDBOX_URL)

    def test_request_without_key_fails_before_the_network(self):
        with patch.object(requests, "request") as call:
            with self.assertRaises(AsaasError):
                AsaasClient("", sandbox=True).get_balance()
        call.assert_not_called()

    def test_api_error_carries_status_and_description(self):
        payload = {"errors": [{"description": "O CPF informado é inválido."}]}
        with patch.object(requests, "request", return_value=FakeResponse(400, payload, "Bad Request")):
            with self.assertRaises(AsaasError) as caught:
                AsaasClient("k").create_payment({})
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("CPF informado é inválido", str(caught.exception))

    def test_connection_failure_becomes_asaas_error(self):
        with patch.object(requests, "request", side_effect=requests.ConnectionError("boom")):
            with self.assertRaises(AsaasError):
                AsaasClient("k").get_balance()

    def test_customer_payload_cleans_document_and_zip(self):
        partner = self.env["res.partner"].create({
            "name": "Livraria Teste",
            "vat": "52.787.529/0001-09",
            "zip": "88036-530",
            "city": "Florianópolis",
            "email": "teste@example.com",
        })
        payload = customer_payload(partner)
        self.assertEqual(payload["cpfCnpj"], "52787529000109")
        self.assertEqual(payload["postalCode"], "88036530")
        self.assertEqual(payload["externalReference"], "odoo-partner-%s" % partner.id)
        self.assertNotIn("phone", payload, "campo vazio não deve ir no corpo")

    def test_ensure_customer_does_not_call_twice(self):
        partner = self.env["res.partner"].create({"name": "Cliente Asaas"})
        client = AsaasClient("k")

        with patch.object(requests, "request", return_value=FakeResponse(payload={"id": "cus_42"})) as call:
            first = partner._asaas_ensure_customer(client)
        self.assertEqual(first, "cus_42")
        self.assertEqual(call.call_count, 1)

        with patch.object(requests, "request") as call:
            again = partner._asaas_ensure_customer(client)
        self.assertEqual(again, "cus_42")
        call.assert_not_called()


@tagged("post_install", "-at_install", "asaas_base")
class TestAsaasWebhookEvent(TransactionCase):

    def test_event_is_stored_even_when_nobody_handles_it(self):
        event = self.env["asaas.webhook.event"].ingest({
            "event": "PAYMENT_RECEIVED", "payment": {"id": "pay_1", "value": 38.0},
        })
        self.assertEqual(event.name, "PAYMENT_RECEIVED")
        self.assertEqual(event.asaas_id, "pay_1")
        self.assertEqual(event.state, "ignored")
        self.assertIn("pay_1", event.payload)

    def test_handler_failure_is_recorded_not_raised(self):
        """Exceção no tratamento não pode virar erro HTTP: o Asaas reenviaria em loop."""
        with patch.object(
            type(self.env["asaas.webhook.event"]), "_dispatch", side_effect=ValueError("boom")
        ):
            event = self.env["asaas.webhook.event"].ingest({"event": "PAYMENT_RECEIVED"})
        self.assertEqual(event.state, "error")
        self.assertIn("boom", event.error)

    def test_legacy_config_key_is_still_read(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("evolars_asaas.api_key", "$aact_legado")
        self.assertEqual(self.env["asaas.config"].get_param("asaas.api_key"), "$aact_legado")

        params.set_param("asaas.api_key", "$aact_novo")
        self.assertEqual(self.env["asaas.config"].get_param("asaas.api_key"), "$aact_novo",
                         "a chave nova tem precedência sobre a legada")

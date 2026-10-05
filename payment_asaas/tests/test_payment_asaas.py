from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.asaas_base.models.asaas_client import AsaasClient, AsaasError


@tagged("post_install", "-at_install", "payment_asaas")
class TestPaymentAsaas(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env.ref("payment_asaas.payment_provider_asaas")
        cls.provider.write({"asaas_api_key": "$aact_hmlg_test", "state": "test"})
        cls.brl = cls.env.ref("base.BRL")
        cls.brl.active = True
        cls.partner = cls.env["res.partner"].create({
            "name": "Compradora Teste",
            "vat": "529.982.247-25",
            "email": "compradora@example.com",
            "city": "São Paulo",
            "zip": "01310-100",
        })

    def _transaction(self, partner=None, amount=38.0):
        return self.env["payment.transaction"].create({
            "provider_id": self.provider.id,
            "payment_method_id": self.provider.payment_method_ids[:1].id,
            "partner_id": (partner or self.partner).id,
            "amount": amount,
            "currency_id": self.brl.id,
            "reference": self.env["payment.transaction"]._compute_reference("asaas"),
        })

    # ------------------------------------------------------------------ #
    # Provedor                                                            #
    # ------------------------------------------------------------------ #

    def test_test_state_always_talks_to_sandbox(self):
        """Provedor em teste não pode emitir cobrança de verdade por descuido."""
        self.provider.state = "test"
        self.assertTrue(self.provider._asaas_get_client().sandbox)
        self.provider.state = "enabled"
        self.assertFalse(self.provider._asaas_get_client().sandbox)

    def _global_key(self, api_key, environment):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("asaas.api_key", api_key)
        params.set_param("asaas.environment", environment)

    def test_empty_provider_key_uses_the_settings_key(self):
        """A chave colada só em Configurações → Asaas basta para o checkout."""
        self.provider.write({"asaas_api_key": False, "state": "enabled"})
        self._global_key("$aact_prod_global", "production")
        client = self.provider._asaas_get_client()
        self.assertEqual(client.api_key, "$aact_prod_global")
        self.assertFalse(client.sandbox)

    def test_provider_key_wins_over_the_settings_key(self):
        self._global_key("$aact_hmlg_global", "sandbox")
        self.assertEqual(self.provider._asaas_get_client().api_key, "$aact_hmlg_test")

    def test_settings_key_from_another_environment_is_never_used(self):
        """Provedor em teste com chave de produção nas Configurações: nada de cobrança real."""
        self.provider.asaas_api_key = False
        self._global_key("$aact_prod_global", "production")
        with self.assertRaises(UserError):
            self.provider._asaas_get_client()

    def test_provider_can_be_enabled_without_its_own_key(self):
        self.provider.write({"asaas_api_key": False, "state": "enabled", "is_published": True})
        self.assertEqual(self.provider.state, "enabled")

    def test_only_brl_is_supported(self):
        names = self.provider._get_supported_currencies().mapped("name")
        self.assertEqual(set(names), {"BRL"})

    def test_offers_pix_boleto_and_card(self):
        self.assertEqual(
            self.provider._get_default_payment_method_codes(), {"pix", "boleto", "card"}
        )

    # ------------------------------------------------------------------ #
    # Criação da cobrança                                                 #
    # ------------------------------------------------------------------ #

    def test_rendering_creates_charge_and_redirects_to_invoice_url(self):
        tx = self._transaction()
        response = {"id": "pay_123", "invoiceUrl": "https://sandbox.asaas.com/i/pay_123"}

        with patch.object(AsaasClient, "create_customer", return_value={"id": "cus_1"}), \
             patch.object(AsaasClient, "create_payment", return_value=response) as create:
            values = tx._get_specific_rendering_values({})

        self.assertEqual(values["api_url"], "https://sandbox.asaas.com/i/pay_123")
        self.assertEqual(tx.provider_reference, "pay_123")

        payload = create.call_args[0][0]
        self.assertEqual(payload["customer"], "cus_1")
        self.assertEqual(payload["billingType"], "UNDEFINED",
                         "o comprador escolhe entre Pix, boleto e cartão na página do Asaas")
        self.assertEqual(payload["value"], 38.0)
        self.assertEqual(payload["externalReference"], tx.reference)
        self.assertTrue(payload["callback"]["successUrl"].endswith("/payment/status"))

    def test_account_without_domain_still_gets_the_charge(self):
        """Conta sem site cadastrado recusa o `callback`: a venda não pode morrer por isso."""
        tx = self._transaction()
        recusa = AsaasError("recusado", status_code=400, payload={"errors": [{
            "code": "invalid_object",
            "description": "Não há nenhum domínio configurado em sua conta. Cadastre um "
                           "site em Minha Conta na aba Informações.",
        }]})
        enviados = []

        def create_payment(client, payload):
            enviados.append(dict(payload))
            if "callback" in payload:
                raise recusa
            return {"id": "pay_9", "invoiceUrl": "https://www.asaas.com/i/pay_9"}

        with patch.object(AsaasClient, "create_customer", return_value={"id": "cus_1"}), \
             patch.object(AsaasClient, "create_payment", autospec=True, side_effect=create_payment):
            values = tx._get_specific_rendering_values({})

        self.assertEqual(values["api_url"], "https://www.asaas.com/i/pay_9")
        self.assertEqual(len(enviados), 2)
        self.assertNotIn("callback", enviados[1])
        self.assertEqual(enviados[1]["externalReference"], tx.reference,
                         "o webhook acha a transação pela referência")

    def test_other_refusals_still_raise(self):
        tx = self._transaction()
        recusa = AsaasError("recusado", status_code=400, payload={"errors": [{
            "code": "invalid_value", "description": "Valor inválido."}]})
        with patch.object(AsaasClient, "create_customer", return_value={"id": "cus_1"}), \
             patch.object(AsaasClient, "create_payment", side_effect=recusa) as create:
            with self.assertRaises(AsaasError):
                tx._get_specific_rendering_values({})
        self.assertEqual(create.call_count, 1)

    def test_customer_without_document_fails_with_a_useful_message(self):
        sem_documento = self.env["res.partner"].create({"name": "Sem CPF"})
        tx = self._transaction(partner=sem_documento)
        with self.assertRaises(ValidationError) as caught:
            tx._get_specific_rendering_values({})
        self.assertIn("CPF", str(caught.exception))

    def test_missing_invoice_url_is_an_error_not_a_silent_redirect(self):
        tx = self._transaction()
        with patch.object(AsaasClient, "create_customer", return_value={"id": "cus_1"}), \
             patch.object(AsaasClient, "create_payment", return_value={"id": "pay_1"}):
            with self.assertRaises(ValidationError):
                tx._get_specific_rendering_values({})

    # ------------------------------------------------------------------ #
    # Retorno de situação                                                 #
    # ------------------------------------------------------------------ #

    def _notify(self, tx, status):
        tx._handle_notification_data("asaas", {
            "id": "pay_999", "status": status, "externalReference": tx.reference,
        })

    def test_confirmed_payment_completes_the_transaction(self):
        tx = self._transaction()
        self._notify(tx, "CONFIRMED")
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.provider_reference, "pay_999")

    def test_pending_payment_stays_pending(self):
        tx = self._transaction()
        self._notify(tx, "PENDING")
        self.assertEqual(tx.state, "pending")

    def test_overdue_payment_is_canceled(self):
        tx = self._transaction()
        self._notify(tx, "OVERDUE")
        self.assertEqual(tx.state, "cancel")

    def test_unknown_status_becomes_an_error_instead_of_being_ignored(self):
        tx = self._transaction()
        self._notify(tx, "SITUACAO_QUE_NAO_EXISTE")
        self.assertEqual(tx.state, "error")

    def test_transaction_is_found_by_external_reference(self):
        tx = self._transaction()
        found = self.env["payment.transaction"]._get_tx_from_notification_data(
            "asaas", {"id": "pay_outro", "externalReference": tx.reference},
        )
        self.assertEqual(found, tx)

    def test_notification_without_matching_transaction_raises(self):
        with self.assertRaises(ValidationError):
            self.env["payment.transaction"]._get_tx_from_notification_data(
                "asaas", {"id": "pay_inexistente", "externalReference": "nao-existe"},
            )

    # ------------------------------------------------------------------ #
    # Webhook                                                             #
    # ------------------------------------------------------------------ #

    def test_webhook_event_updates_the_transaction(self):
        tx = self._transaction()
        event = self.env["asaas.webhook.event"].ingest({
            "event": "PAYMENT_RECEIVED",
            "payment": {"id": "pay_web", "status": "RECEIVED", "externalReference": tx.reference},
        })
        self.assertEqual(event.state, "done")
        self.assertEqual(tx.state, "done")

    def test_shop_charge_is_not_mirrored_by_the_billing_module(self):
        """Com o evolars_asaas junto, a cobrança da loja não vira cobrança do financeiro.

        Viraria uma segunda notificação ao comprador e o recebimento lançado duas
        vezes. Antes, ainda quebrava com billingType UNDEFINED.
        """
        if "evolars.asaas.payment" not in self.env:
            self.skipTest("evolars_asaas não instalado")
        tx = self._transaction()
        for event_id, name, status in (("evt_1", "PAYMENT_CREATED", "PENDING"),
                                       ("evt_2", "PAYMENT_RECEIVED", "RECEIVED")):
            event = self.env["asaas.webhook.event"].ingest({
                "id": event_id, "event": name,
                "payment": {"id": "pay_loja", "status": status, "billingType": "UNDEFINED",
                            "value": 38.0, "externalReference": tx.reference},
            })
            self.assertEqual(event.state, "done", event.error)
        self.assertEqual(tx.state, "done")
        self.assertFalse(self.env["evolars.asaas.payment"].search(
            [("asaas_payment_id", "=", "pay_loja")]))

    def test_charge_created_outside_the_shop_is_ignored_not_an_error(self):
        """Cobrança de faturamento ou assinatura não tem transação — é normal."""
        event = self.env["asaas.webhook.event"].ingest({
            "event": "PAYMENT_RECEIVED",
            "payment": {"id": "pay_faturamento", "status": "RECEIVED",
                        "externalReference": "contrato-2026-001"},
        })
        self.assertEqual(event.state, "ignored")

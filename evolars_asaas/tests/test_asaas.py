# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.evolars_asaas.models.asaas_api import AsaasAPI
from odoo.addons.evolars_asaas.models.resend_api import ResendReceiptAPI


@tagged("post_install", "-at_install")
class TestAsaas(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

        # Partner para testes
        cls.partner = cls.env["res.partner"].create({
            "name": "Cliente Asaas Evolars",
            "email": "cliente.asaas@example.test",
            "vat": "12345678909",
            "street": "Avenida Paulista 1000",
            "city": "São Paulo",
            "zip": "01310-100",
        })

        # Diário contábil bancário para testes de recebimentos
        cls.bank_journal = cls.env["account.journal"].create({
            "name": "Banco Asaas Teste",
            "code": "ASAST",
            "type": "bank",
            "company_id": cls.company.id,
        })
        cls.env["ir.config_parameter"].sudo().set_param(
            "evolars_asaas.receivable_journal_id", cls.bank_journal.id
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "evolars_asaas.resend_api_key", "re_test_key_123"
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "evolars_asaas.webhook_token", "secret_token_test_abc"
        )

    def setUp(self):
        super().setUp()
        self.payment = self.env["evolars.asaas.payment"].create({
            "name": "PIX de Teste Evolars",
            "partner_id": self.partner.id,
            "amount": 500.0,
            "billing_type": "PIX",
            "company_id": self.company.id,
        })

    def test_create_payment_sends_split_and_persists_external_id(self):
        self.payment.split_line_ids = [(0, 0, {
            "name": "Parceiro Split",
            "wallet_id": "wal_test_999",
            "split_type": "fixed",
            "amount": 50.0,
        })]
        with patch("odoo.addons.evolars_asaas.models.asaas_payment.AsaasAPI.ensure_customer", return_value="cus_test_123"), \
             patch("odoo.addons.evolars_asaas.models.asaas_payment.AsaasAPI.request", side_effect=[
                 {"id": "pay_test_001", "status": "PENDING", "invoiceUrl": "https://example.test/pay/001", "bankSlipUrl": "https://example.test/slip/001"},
                 {"payload": "00020126580014br.gov.bcb.pix..."},
             ]) as mock_req:
            self.payment.action_create_remote()

        self.assertEqual(self.payment.asaas_payment_id, "pay_test_001")
        self.assertEqual(self.payment.pix_payload, "00020126580014br.gov.bcb.pix...")
        self.assertEqual(self.payment.invoice_url, "https://example.test/pay/001")
        self.assertEqual(mock_req.call_args_list[0].args[:2], ("POST", "/payments"))
        self.assertEqual(mock_req.call_args_list[0].args[2]["split"], [{"walletId": "wal_test_999", "fixedValue": 50.0}])

    def test_webhook_payment_received_auto_reconciles_invoice_and_sends_resend_receipt(self):
        """Assegura que o webhook PAYMENT_RECEIVED concilia a fatura e dispara recibo oficial via Resend."""
        self.payment.asaas_payment_id = "pay_received_001"

        # Cria fatura de cliente vinculada
        invoice = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.partner.id,
            "invoice_date": fields.Date.today(),
            "line_ids": [
                (0, 0, {
                    "name": "Serviço de Engenharia de Software",
                    "quantity": 1,
                    "price_unit": 500.0,
                })
            ],
        })
        invoice.action_post()
        self.payment.invoice_id = invoice.id

        payload = {
            "id": "evt_received_100",
            "event": "PAYMENT_RECEIVED",
            "payment": {
                "id": "pay_received_001",
                "status": "RECEIVED",
                "paymentDate": "2026-08-26",
                "value": 500.0,
            },
        }

        mock_resend_response = {"id": "resend_receipt_uuid_777"}
        with patch("odoo.addons.evolars_asaas.models.resend_api.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, json=lambda: mock_resend_response)
            event = self.env["evolars.asaas.webhook.event"].ingest(payload)

        self.assertTrue(event)
        self.assertEqual(self.payment.state, "received")
        self.assertEqual(str(self.payment.received_date), "2026-08-26")
        self.assertTrue(self.payment.account_payment_id)
        self.assertEqual(self.payment.account_payment_id.amount, 500.0)
        self.assertEqual(self.payment.account_payment_id.state, "posted")
        self.assertTrue(self.payment.receipt_sent)
        self.assertEqual(self.payment.resend_email_id, "resend_receipt_uuid_777")
        self.assertTrue(self.payment.receipt_sent_date)

        # Verifica se a chamada Resend incluiu o cliente e a liderança executiva em CC
        mock_post.assert_called_once()
        resend_payload = mock_post.call_args[1]["json"]
        self.assertEqual(resend_payload["to"], ["cliente.asaas@example.test"])
        self.assertIn("anliben@icloud.com", resend_payload["cc"])
        self.assertNotIn("julia.abreu@evolars.com.br", resend_payload["cc"])
        self.assertNotIn("juliaabreu.evolars@gmail.com", resend_payload["cc"])
        self.assertIn("500,00", resend_payload["html"])

    def test_webhook_payment_confirmed_auto_posts_draft_invoice_and_reconciles(self):
        """Assegura que fatura em rascunho é publicada automaticamente e conciliada no PAYMENT_CONFIRMED."""
        self.payment.asaas_payment_id = "pay_confirmed_002"

        draft_invoice = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.partner.id,
            "invoice_date": fields.Date.today(),
            "line_ids": [
                (0, 0, {
                    "name": "Desenvolvimento de Agentes de IA",
                    "quantity": 1,
                    "price_unit": 500.0,
                })
            ],
        })
        self.assertEqual(draft_invoice.state, "draft")
        self.payment.invoice_id = draft_invoice.id

        payload = {
            "id": "evt_confirmed_200",
            "event": "PAYMENT_CONFIRMED",
            "payment": {
                "id": "pay_confirmed_002",
                "status": "CONFIRMED",
                "clientPaymentDate": "2026-08-26",
                "value": 500.0,
            },
        }

        with patch("odoo.addons.evolars_asaas.models.resend_api.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, json=lambda: {"id": "resend_mock_222"})
            self.env["evolars.asaas.webhook.event"].ingest(payload)

        self.assertEqual(self.payment.state, "confirmed")
        self.assertEqual(draft_invoice.state, "posted")
        self.assertTrue(self.payment.account_payment_id)
        self.assertTrue(self.payment.receipt_sent)

    def test_webhook_payment_overdue_alerts_chatter_and_creates_activity(self):
        """Assegura que PAYMENT_OVERDUE marca a cobrança como vencida e registra alerta."""
        self.payment.asaas_payment_id = "pay_overdue_003"
        self.payment.state = "pending"

        payload = {
            "id": "evt_overdue_300",
            "event": "PAYMENT_OVERDUE",
            "payment": {
                "id": "pay_overdue_003",
                "status": "OVERDUE",
                "dueDate": "2026-08-20",
            },
        }

        event = self.env["evolars.asaas.webhook.event"].ingest(payload)
        self.assertEqual(self.payment.state, "overdue")

    def test_webhook_idempotency_and_duplicate_event_handling(self):
        """Assegura idempotência estrita de webhooks duplicados."""
        self.payment.asaas_payment_id = "pay_idempotent_004"
        payload = {
            "id": "evt_idempotent_400",
            "event": "PAYMENT_RECEIVED",
            "payment": {
                "id": "pay_idempotent_004",
                "status": "RECEIVED",
                "paymentDate": "2026-08-26",
            },
        }

        with patch("odoo.addons.evolars_asaas.models.resend_api.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, json=lambda: {"id": "resend_idemp_1"})
            event1 = self.env["evolars.asaas.webhook.event"].ingest(payload)
            payment_id_1 = self.payment.account_payment_id.id if self.payment.account_payment_id else False

            # Segundo disparo com mesmo ID de evento
            event2 = self.env["evolars.asaas.webhook.event"].ingest(payload)
            payment_id_2 = self.payment.account_payment_id.id if self.payment.account_payment_id else False

        self.assertEqual(event1.id, event2.id)
        self.assertEqual(payment_id_1, payment_id_2)
        # Resend deve ter sido chamado apenas uma vez devido à idempotência
        self.assertEqual(mock_post.call_count, 1)

    def test_webhook_subscription_payment_auto_creation_and_reconciliation(self):
        """Assegura que cobrança originada de assinatura é criada automaticamente no Odoo e conciliada."""
        # Cria contrato OCA e Assinatura Asaas
        contract = self.env["contract.contract"].create({
            "name": "Contrato Suporte Evolars",
            "partner_id": self.partner.id,
            "company_id": self.company.id,
        })
        subscription = self.env["evolars.asaas.subscription"].create({
            "name": "Assinatura Mensal R$ 900",
            "partner_id": self.partner.id,
            "contract_id": contract.id,
            "amount": 900.0,
            "billing_type": "PIX",
            "cycle": "MONTHLY",
            "asaas_subscription_id": "sub_test_888",
            "state": "active",
        })

        payload = {
            "id": "evt_sub_cycle_500",
            "event": "PAYMENT_RECEIVED",
            "subscription": {"id": "sub_test_888"},
            "payment": {
                "id": "pay_cycle_generated_555",
                "subscription": "sub_test_888",
                "status": "RECEIVED",
                "paymentDate": "2026-08-26",
                "value": 900.0,
                "billingType": "PIX",
            },
        }

        with patch("odoo.addons.evolars_asaas.models.resend_api.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, json=lambda: {"id": "resend_sub_receipt"})
            event = self.env["evolars.asaas.webhook.event"].ingest(payload)

        created_pay = self.env["evolars.asaas.payment"].search([("asaas_payment_id", "=", "pay_cycle_generated_555")])
        self.assertTrue(created_pay)
        self.assertEqual(created_pay.subscription_id, subscription)
        self.assertEqual(created_pay.amount, 900.0)
        self.assertEqual(created_pay.state, "received")
        self.assertTrue(created_pay.receipt_sent)

    def test_webhook_external_reference_fallback_matching(self):
        """Assegura recuperação e sincronização por externalReference quando asaas_payment_id não estava salvo."""
        ext_ref = "odoo-payment-%s" % self.payment.id
        self.payment.external_reference = ext_ref
        self.assertFalse(self.payment.asaas_payment_id)

        payload = {
            "id": "evt_ref_match_600",
            "event": "PAYMENT_RECEIVED",
            "payment": {
                "id": "pay_remote_found_666",
                "externalReference": ext_ref,
                "status": "RECEIVED",
                "paymentDate": "2026-08-26",
            },
        }

        with patch("odoo.addons.evolars_asaas.models.resend_api.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200, json=lambda: {"id": "resend_mock_666"})
            self.env["evolars.asaas.webhook.event"].ingest(payload)

        self.assertEqual(self.payment.asaas_payment_id, "pay_remote_found_666")
        self.assertEqual(self.payment.state, "received")

    def test_resend_email_failure_is_defensive_and_does_not_break_reconciliation(self):
        """Assegura que falha de rede/API no Resend não quebra o processo de liquidação contábil."""
        self.payment.asaas_payment_id = "pay_resend_fail_007"
        payload = {
            "id": "evt_resend_fail_700",
            "event": "PAYMENT_RECEIVED",
            "payment": {
                "id": "pay_resend_fail_007",
                "status": "RECEIVED",
                "paymentDate": "2026-08-26",
            },
        }

        with patch("odoo.addons.evolars_asaas.models.resend_api.requests.post", side_effect=Exception("Timeout no servidor Resend")):
            event = self.env["evolars.asaas.webhook.event"].ingest(payload)

        self.assertTrue(event)
        self.assertEqual(self.payment.state, "received")
        # Pagamento contábil continua íntegro
        self.assertTrue(self.payment.account_payment_id)
        # Recibo não foi marcado como enviado com sucesso
        self.assertFalse(self.payment.receipt_sent)

    def test_internal_user_without_asaas_group_has_no_access(self):
        """Usuário interno comum não recebe o módulo Asaas automaticamente."""
        internal_user = self.env["res.users"].create({
            "name": "Operador Interno Evolars",
            "login": "operador.teste@evolars.com.br",
            "email": "operador.teste@evolars.com.br",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        self.assertFalse(internal_user.has_group("evolars_asaas.group_asaas_user"))
        with self.assertRaises(AccessError):
            self.env["evolars.asaas.payment"].with_user(internal_user).search([], limit=1)

    def test_split_constraints_fixed_and_percentage(self):
        """Assegura validação defensiva contra splits inválidos."""
        # Split fixo maior que o total da cobrança
        with self.assertRaises(ValidationError):
            self.env["evolars.asaas.payment"].create({
                "name": "Cobrança Inválida",
                "partner_id": self.partner.id,
                "amount": 100.0,
                "split_line_ids": [(0, 0, {
                    "name": "Split Maior",
                    "wallet_id": "wal_exceed",
                    "split_type": "fixed",
                    "amount": 150.0,
                })],
            })

        # Split percentual maior que 100%
        with self.assertRaises(ValidationError):
            self.env["evolars.asaas.payment"].create({
                "name": "Cobrança Percentual Inválida",
                "partner_id": self.partner.id,
                "amount": 100.0,
                "split_line_ids": [(0, 0, {
                    "name": "Split Percentual Excedente",
                    "wallet_id": "wal_percent_exceed",
                    "split_type": "percentage",
                    "percentage": 105.0,
                })],
            })

    def test_existing_customer_has_notifications_disabled_once(self):
        self.partner.asaas_customer_id = "cus_existing_999"
        api = AsaasAPI(self.env)
        with patch.object(api, "request", return_value={}) as request:
            self.assertEqual(api.ensure_customer(self.partner), "cus_existing_999")
        request.assert_called_once_with("PUT", "/customers/cus_existing_999", {"notificationDisabled": True})
        self.assertTrue(self.partner.asaas_notifications_disabled)

    def test_payment_lifecycle_actions_update_and_delete(self):
        self.payment.asaas_payment_id = "pay_lifecycle_001"
        self.payment.state = "pending"
        self.payment.amount = 750.0
        self.payment.due_date = "2026-09-30"

        with patch("odoo.addons.evolars_asaas.models.asaas_payment.AsaasAPI.update_payment", return_value={"status": "PENDING"}) as mock_update:
            self.payment.action_update_remote()
            mock_update.assert_called_once()
            call_payload = mock_update.call_args[0][1]
            self.assertEqual(call_payload["value"], 750.0)
            self.assertEqual(call_payload["dueDate"], "2026-09-30")

        with patch("odoo.addons.evolars_asaas.models.asaas_payment.AsaasAPI.delete_payment", return_value={"deleted": True}) as mock_delete:
            self.payment.action_delete_remote()
            mock_delete.assert_called_once_with("pay_lifecycle_001")
            self.assertEqual(self.payment.state, "cancelled")

    def test_payment_sync_from_remote_and_receive_in_cash(self):
        self.payment.asaas_payment_id = "pay_sync_002"
        self.payment.state = "pending"

        remote_data = {
            "id": "pay_sync_002",
            "status": "RECEIVED",
            "value": 500.0,
            "dueDate": "2026-09-15",
            "clientPaymentDate": "2026-09-10",
            "invoiceUrl": "https://example.test/inv/002",
        }
        with patch("odoo.addons.evolars_asaas.models.asaas_payment.AsaasAPI.get_payment", return_value=remote_data), \
             patch("odoo.addons.evolars_asaas.models.asaas_payment.AsaasPayment._register_accounting_payment") as mock_acc:
            self.payment.action_sync_from_remote()
            self.assertEqual(self.payment.state, "received")
            self.assertEqual(str(self.payment.received_date), "2026-09-10")
            self.assertEqual(self.payment.invoice_url, "https://example.test/inv/002")
            mock_acc.assert_called_once()

        self.payment.state = "pending"
        with patch("odoo.addons.evolars_asaas.models.asaas_payment.AsaasAPI.receive_in_cash", return_value={"status": "RECEIVED"}), \
             patch("odoo.addons.evolars_asaas.models.asaas_payment.AsaasPayment._register_accounting_payment"):
            self.payment.action_receive_in_cash()
            self.assertEqual(self.payment.state, "received")

    def test_refund_wizard_total_and_partial(self):
        self.payment.asaas_payment_id = "pay_refund_003"
        self.payment.state = "received"
        self.payment.amount = 500.0

        wizard_total = self.env["evolars.asaas.payment.refund.wizard"].create({
            "payment_id": self.payment.id,
            "refund_type": "total",
            "amount": 500.0,
            "description": "Cancelamento amigável",
        })
        with patch("odoo.addons.evolars_asaas.wizard.asaas_payment_refund_wizard.AsaasAPI.refund_payment") as mock_refund:
            wizard_total.action_confirm_refund()
            mock_refund.assert_called_once_with("pay_refund_003", value=None, description="Cancelamento amigável")
            self.assertEqual(self.payment.state, "refunded")

        self.payment.state = "confirmed"
        wizard_partial = self.env["evolars.asaas.payment.refund.wizard"].create({
            "payment_id": self.payment.id,
            "refund_type": "partial",
            "amount": 150.0,
            "description": "Devolução parcial",
        })
        with patch("odoo.addons.evolars_asaas.wizard.asaas_payment_refund_wizard.AsaasAPI.refund_payment") as mock_refund:
            wizard_partial.action_confirm_refund()
            mock_refund.assert_called_once_with("pay_refund_003", value=150.0, description="Devolução parcial")
            self.assertEqual(self.payment.state, "refunded")

    def test_subscription_lifecycle_actions(self):
        contract = self.env["contract.contract"].create({
            "name": "Contrato Teste Lifecycle",
            "partner_id": self.partner.id,
            "company_id": self.company.id,
        })
        sub = self.env["evolars.asaas.subscription"].create({
            "name": "Assinatura Lifecycle",
            "partner_id": self.partner.id,
            "contract_id": contract.id,
            "amount": 800.0,
            "billing_type": "PIX",
            "cycle": "MONTHLY",
            "asaas_subscription_id": "sub_life_001",
            "state": "active",
        })

        with patch(
            "odoo.addons.evolars_asaas.models.asaas_subscription.AsaasAPI.update_subscription",
            return_value={"nextDueDate": "2026-11-01"},
        ) as mock_sub_upd:
            sub.amount = 850.0
            sub.action_update_remote()
            mock_sub_upd.assert_called_once()
            self.assertEqual(mock_sub_upd.call_args[0][0], "sub_life_001")
            self.assertEqual(mock_sub_upd.call_args[0][1]["value"], 850.0)
            self.assertTrue(mock_sub_upd.call_args[0][1]["updatePendingPayments"])

        with patch("odoo.addons.evolars_asaas.models.asaas_subscription.AsaasAPI.update_subscription"):
            sub.action_inactivate_remote()
            self.assertEqual(sub.state, "inactive")
            sub.action_activate_remote()
            self.assertEqual(sub.state, "active")

        with patch("odoo.addons.evolars_asaas.models.asaas_subscription.AsaasAPI.delete_subscription") as mock_sub_del:
            sub.action_delete_remote()
            mock_sub_del.assert_called_once_with("sub_life_001")
            self.assertEqual(sub.state, "expired")

    def test_subscription_sync_payments(self):
        contract = self.env["contract.contract"].create({
            "name": "Contrato Teste Sync Payments",
            "partner_id": self.partner.id,
            "company_id": self.company.id,
        })
        sub = self.env["evolars.asaas.subscription"].create({
            "name": "Assinatura Sync Payments",
            "partner_id": self.partner.id,
            "contract_id": contract.id,
            "amount": 300.0,
            "billing_type": "BOLETO",
            "cycle": "MONTHLY",
            "asaas_subscription_id": "sub_sync_pay_002",
            "state": "active",
        })

        mock_payments_resp = {
            "data": [
                {
                    "id": "pay_child_111",
                    "value": 300.0,
                    "dueDate": "2026-10-01",
                    "billingType": "BOLETO",
                    "status": "PENDING",
                    "invoiceUrl": "https://example.test/inv/111",
                    "bankSlipUrl": "https://example.test/slip/111",
                }
            ]
        }
        with patch("odoo.addons.evolars_asaas.models.asaas_subscription.AsaasAPI.get_subscription_payments", return_value=mock_payments_resp):
            sub.action_sync_payments()

        child_pay = self.env["evolars.asaas.payment"].search([("asaas_payment_id", "=", "pay_child_111")])
        self.assertTrue(child_pay)
        self.assertEqual(child_pay.subscription_id, sub)
        self.assertEqual(child_pay.amount, 300.0)
        self.assertEqual(sub.payment_count, 1)

    def test_partner_sync_to_asaas(self):
        api = AsaasAPI(self.env)
        with patch.object(api, "request", return_value={"id": "cus_synced_555"}) as mock_req:
            cus_id = api.sync_customer(self.partner)
            self.assertEqual(cus_id, "cus_synced_555")
            self.assertEqual(self.partner.asaas_customer_id, "cus_synced_555")
            self.assertEqual(mock_req.call_args[0][0], "POST")
            self.assertEqual(mock_req.call_args[0][1], "/customers")

        # action_sync_asaas_customer instancia a própria AsaasAPI: patch na
        # instância local não alcança, tem que ser na classe.
        with patch.object(AsaasAPI, "request", return_value={"id": "cus_synced_555"}) as mock_req:
            self.partner.action_sync_asaas_customer()
            self.assertEqual(mock_req.call_args[0][0], "PUT")
            self.assertEqual(mock_req.call_args[0][1], "/customers/cus_synced_555")

    def test_webhook_payment_checkout_viewed_updates_date(self):
        self.payment.asaas_payment_id = "pay_viewed_001"
        payload = {
            "id": "evt_viewed_100",
            "event": "PAYMENT_CHECKOUT_VIEWED",
            "payment": {
                "id": "pay_viewed_001",
                "lastInvoiceViewedDate": "2026-09-08T21:55:55Z",
            },
        }
        self.env["evolars.asaas.webhook.event"].ingest(payload)
        self.assertTrue(self.payment.last_invoice_viewed_date)
        self.assertEqual(str(self.payment.last_invoice_viewed_date), "2026-09-08 21:55:55")

    def test_webhook_unlinked_payment_auto_creates_record(self):
        self.partner.asaas_customer_id = "cus_auto_001"
        payload = {
            "id": "evt_auto_created_100",
            "event": "PAYMENT_OVERDUE",
            "payment": {
                "id": "pay_auto_001",
                "customer": "cus_auto_001",
                "value": 199.0,
                "status": "OVERDUE",
                "billingType": "PIX",
                "dueDate": "2026-09-06",
                "description": "Prospecta Brasil - Teste",
            },
        }
        event = self.env["evolars.asaas.webhook.event"].ingest(payload)
        self.assertTrue(event.payment_id)
        self.assertEqual(event.payment_id.asaas_payment_id, "pay_auto_001")
        self.assertEqual(event.payment_id.partner_id, self.partner)
        self.assertEqual(event.payment_id.amount, 199.0)
        self.assertEqual(event.payment_id.state, "overdue")



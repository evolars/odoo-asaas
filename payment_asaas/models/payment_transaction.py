import logging
from datetime import timedelta

from odoo import _, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.payment_asaas import const

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    def _get_specific_rendering_values(self, processing_values):
        """Cria a cobrança no Asaas e devolve para onde mandar o comprador."""
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != "asaas":
            return res

        client = self.provider_id._asaas_get_client()
        partner = self.partner_id
        if not partner.vat:
            # O Asaas recusa a cobrança sem CPF/CNPJ. Melhor dizer isso aqui, com
            # o nome do campo, do que deixar a API responder um erro genérico.
            raise ValidationError(_(
                "Informe o CPF ou CNPJ de %s para pagar pelo Asaas.", partner.display_name
            ))

        customer_id = partner._asaas_ensure_customer(client)
        payment = client.create_payment(self._asaas_prepare_payment_payload(customer_id))

        self.provider_reference = payment["id"]
        invoice_url = payment.get("invoiceUrl")
        if not invoice_url:
            raise ValidationError(_("O Asaas não devolveu a página de pagamento da cobrança."))
        return {"api_url": invoice_url, "url_params": {}}

    def _asaas_prepare_payment_payload(self, customer_id):
        self.ensure_one()
        base_url = self.provider_id.get_base_url().rstrip("/")
        due_date = fields.Date.context_today(self) + timedelta(days=const.DEFAULT_DUE_DAYS)
        return {
            "customer": customer_id,
            # UNDEFINED deixa o comprador escolher Pix, boleto ou cartão na página do Asaas
            "billingType": "UNDEFINED",
            "value": float(self.amount),
            "dueDate": due_date.isoformat(),
            "description": self.reference,
            "externalReference": self.reference,
            "callback": {
                "successUrl": "%s/payment/status" % base_url,
                "autoRedirect": True,
            },
        }

    def _get_tx_from_notification_data(self, provider_code, notification_data):
        tx = super()._get_tx_from_notification_data(provider_code, notification_data)
        if provider_code != "asaas" or len(tx) == 1:
            return tx

        reference = notification_data.get("externalReference")
        if reference:
            tx = self.search([("reference", "=", reference), ("provider_code", "=", "asaas")])
        if not tx and notification_data.get("id"):
            tx = self.search([
                ("provider_reference", "=", notification_data["id"]),
                ("provider_code", "=", "asaas"),
            ])
        if not tx:
            raise ValidationError(_(
                "Asaas: nenhuma transação encontrada para a cobrança %s.",
                notification_data.get("id") or reference or "?",
            ))
        return tx

    def _process_notification_data(self, notification_data):
        super()._process_notification_data(notification_data)
        if self.provider_code != "asaas":
            return

        if not self.provider_reference and notification_data.get("id"):
            self.provider_reference = notification_data["id"]

        status = (notification_data.get("status") or "").upper()
        if not status:
            self._set_error(_("Asaas: a cobrança chegou sem situação."))
            return

        if status in const.PAYMENT_STATUS_MAPPING["pending"]:
            self._set_pending()
        elif status in const.PAYMENT_STATUS_MAPPING["done"]:
            self._set_done()
        elif status in const.PAYMENT_STATUS_MAPPING["cancel"]:
            self._set_canceled(state_message=_("Asaas informou a situação %s.", status))
        elif status in const.PAYMENT_STATUS_MAPPING["error"]:
            self._set_error(_("Asaas informou a situação %s.", status))
        else:
            _logger.info("Asaas: situação não mapeada '%s' na transação %s.", status, self.reference)
            self._set_error(_("Asaas devolveu uma situação desconhecida: %s.", status))

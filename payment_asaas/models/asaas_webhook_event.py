import logging

from odoo import models

_logger = logging.getLogger(__name__)

# Eventos que mudam a situação de uma cobrança. Os demais (visualizou o boleto,
# abriu o checkout, split concluído) ficam registrados sem mexer na transação.
RELEVANT_EVENTS = (
    "PAYMENT_CREATED", "PAYMENT_UPDATED", "PAYMENT_CONFIRMED", "PAYMENT_RECEIVED",
    "PAYMENT_OVERDUE", "PAYMENT_DELETED", "PAYMENT_RESTORED", "PAYMENT_REFUNDED",
    "PAYMENT_PARTIALLY_REFUNDED", "PAYMENT_REFUND_IN_PROGRESS",
    "PAYMENT_CHARGEBACK_REQUESTED", "PAYMENT_CHARGEBACK_DISPUTE",
    "PAYMENT_AWAITING_CHARGEBACK_REVERSAL", "PAYMENT_CREDIT_CARD_CAPTURE_REFUSED",
    "PAYMENT_RECEIVED_IN_CASH_UNDONE",
)


class AsaasWebhookEvent(models.Model):
    _inherit = "asaas.webhook.event"

    def _dispatch(self, payload):
        handled = super()._dispatch(payload)
        event = payload.get("event")
        payment = payload.get("payment") or {}
        if event not in RELEVANT_EVENTS or not payment:
            return handled

        Transaction = self.env["payment.transaction"].sudo()
        try:
            tx = Transaction._get_tx_from_notification_data("asaas", payment)
        except Exception:
            # Cobrança criada fora da loja (faturamento, assinatura) não tem
            # transação — é caso normal, não erro.
            _logger.debug("Asaas: %s sem transação correspondente (%s).", event, payment.get("id"))
            return handled

        tx._handle_notification_data("asaas", payment)
        return True

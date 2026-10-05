from odoo import models


class AsaasWebhookEvent(models.Model):
    """Liga a recepção do `asaas_base` ao processamento deste módulo.

    O endpoint HTTP e a autenticação passaram a ser do `asaas_base` — um só,
    compartilhado com o provedor de pagamento. O processamento continua aqui,
    em `evolars.asaas.webhook.event`, com a deduplicação por `external_event_id`
    e a conciliação que já existiam.
    """

    _inherit = "asaas.webhook.event"

    def _dispatch(self, payload):
        handled = super()._dispatch(payload)
        if not payload.get("id") or not payload.get("event"):
            return handled
        if self._is_shop_charge(payload):
            # Cobrança do checkout do site: quem cuida é o `payment_asaas`, pela
            # transação. Espelhá-la aqui mandaria uma segunda notificação ao
            # comprador e lançaria o recebimento duas vezes no financeiro.
            return handled
        self.env["evolars.asaas.webhook.event"].sudo().ingest(payload)
        return True

    def _is_shop_charge(self, payload):
        """A cobrança tem transação de pagamento do Odoo (checkout do site)?

        Checado pelo dado, e não pela ordem dos `_dispatch`: a ordem de herança
        muda conforme a ordem de instalação dos módulos.
        """
        payment = payload.get("payment") or {}
        if not payment or "payment.transaction" not in self.env:
            return False
        domain = [("provider_id.code", "=", "asaas")]
        keys = []
        if payment.get("externalReference"):
            keys.append(("reference", "=", payment["externalReference"]))
        if payment.get("id"):
            keys.append(("provider_reference", "=", payment["id"]))
        if not keys:
            return False
        domain += ["|"] * (len(keys) - 1) + keys
        return bool(self.env["payment.transaction"].sudo().search_count(domain, limit=1))

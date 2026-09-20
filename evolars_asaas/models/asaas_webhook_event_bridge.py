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
        self.env["evolars.asaas.webhook.event"].sudo().ingest(payload)
        return True

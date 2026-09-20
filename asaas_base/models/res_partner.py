from odoo import _, fields, models

from .asaas_client import customer_payload


class ResPartner(models.Model):
    _inherit = "res.partner"

    asaas_customer_id = fields.Char(copy=False, index=True, readonly=True)
    asaas_notifications_disabled = fields.Boolean(copy=False, readonly=True)

    def _asaas_sync_customer(self, client, notification_disabled=True):
        """Cria ou atualiza o cliente no Asaas e devolve o id de lá."""
        self.ensure_one()
        payload = customer_payload(self, notification_disabled=notification_disabled)
        if self.asaas_customer_id:
            client.update_customer(self.asaas_customer_id, payload)
        else:
            result = client.create_customer(payload)
            self.sudo().asaas_customer_id = result["id"]
        self.sudo().asaas_notifications_disabled = notification_disabled
        return self.asaas_customer_id

    def _asaas_ensure_customer(self, client, notification_disabled=True):
        """Devolve o id do cliente, criando lá só se ainda não existir.

        Diferente de `_asaas_sync_customer`, não gasta uma chamada quando o
        cliente já está espelhado — importante no checkout, que está no caminho
        do comprador.
        """
        self.ensure_one()
        if self.asaas_customer_id:
            return self.asaas_customer_id
        return self._asaas_sync_customer(client, notification_disabled=notification_disabled)

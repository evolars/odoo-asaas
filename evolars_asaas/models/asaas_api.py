"""Adaptador sobre o cliente compartilhado do `asaas_base`.

O cliente de API saiu daqui e virou `asaas_base.AsaasClient`, sem acoplamento com
o Odoo, para ser usado também pelo provedor de pagamento. Esta classe mantém a
assinatura antiga — `AsaasAPI(env)` e os mesmos métodos — para que o resto do
módulo continue funcionando sem alteração.

Código novo deve usar `env["asaas.config"].get_client()` direto.
"""
from odoo import _
from odoo.exceptions import UserError

from odoo.addons.asaas_base.models.asaas_client import (  # noqa: F401 - reexportado
    AsaasClient,
    AsaasError,
    customer_payload,
)


class AsaasAPI(AsaasClient):
    def __init__(self, env):
        self.env = env
        config = env["asaas.config"]
        super().__init__(
            api_key=config.get_param("asaas.api_key"),
            sandbox=config.is_sandbox(),
        )

    # ------------------------------------------------------------------ #
    # Clientes — a parte que toca o res.partner                           #
    # ------------------------------------------------------------------ #

    def _customer_payload(self, partner):
        return customer_payload(partner)

    def sync_customer(self, partner):
        if not partner.name:
            raise UserError(_("O cliente precisa ter nome para ser enviado ao Asaas."))
        return partner._asaas_sync_customer(self)

    def ensure_customer(self, partner):
        """Garante o cliente no Asaas, com as notificações de lá desligadas.

        Quem avisa o devedor é o Odoo — deixar o Asaas avisar também faria a
        cobrança chegar em duplicata.
        """
        if partner.asaas_customer_id and not partner.asaas_notifications_disabled:
            self.update_customer(partner.asaas_customer_id, {"notificationDisabled": True})
            partner.sudo().asaas_notifications_disabled = True
            return partner.asaas_customer_id
        return partner._asaas_ensure_customer(self)

    # ------------------------------------------------------------------ #
    # Webhook                                                             #
    # ------------------------------------------------------------------ #

    def get_webhook(self, webhook_id):
        return self.request("GET", "/webhooks/%s" % webhook_id)

    def sync_webhook(self, target_url=None, auth_token=None, email=None, events=None):
        config = self.env["asaas.config"]
        return super().sync_webhook(
            name="Odoo Evolars",
            target_url=target_url or config.webhook_url(),
            auth_token=auth_token or config.get_param("asaas.webhook_token") or "",
            email=(
                email
                or self.env.user.email
                or self.env.company.email
                or "contato@evolars.com.br"
            ),
            events=events,
        )

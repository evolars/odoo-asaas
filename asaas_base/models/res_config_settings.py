from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .asaas_client import AsaasClient

# Chaves antigas, de quando o cliente de API morava dentro do evolars_asaas.
# Bases que já rodavam continuam lendo daqui enquanto ninguém salvou as novas.
LEGACY_PARAMS = {
    "asaas.api_key": "evolars_asaas.api_key",
    "asaas.environment": "evolars_asaas.environment",
    "asaas.webhook_token": "evolars_asaas.webhook_token",
}


class AsaasConfig(models.AbstractModel):
    _name = "asaas.config"
    _description = "Configuração e fábrica de clientes Asaas"

    @api.model
    def get_param(self, key, default=None):
        params = self.env["ir.config_parameter"].sudo()
        value = params.get_param(key)
        if not value and key in LEGACY_PARAMS:
            value = params.get_param(LEGACY_PARAMS[key])
        return value or default

    @api.model
    def is_sandbox(self):
        return self.get_param("asaas.environment", "sandbox") == "sandbox"

    @api.model
    def get_client(self):
        """Cliente montado com a credencial global das Configurações."""
        api_key = self.get_param("asaas.api_key")
        if not api_key:
            raise UserError(_(
                "Configure a chave de API do Asaas em Configurações → Asaas."
            ))
        return AsaasClient(api_key, sandbox=self.is_sandbox())

    @api.model
    def webhook_url(self):
        base = (self.get_param("web.base.url") or "").rstrip("/")
        return "%s/asaas/webhook" % base


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    asaas_api_key = fields.Char(
        string="Chave de API do Asaas",
        config_parameter="asaas.api_key",
        groups="base.group_system",
    )
    asaas_environment = fields.Selection(
        [("sandbox", "Sandbox"), ("production", "Produção")],
        string="Ambiente Asaas",
        config_parameter="asaas.environment",
        default="sandbox",
        required=True,
    )
    asaas_webhook_token = fields.Char(
        string="Token do Webhook",
        config_parameter="asaas.webhook_token",
        groups="base.group_system",
        help="Segredo combinado com o Asaas. Chega em cada evento no cabeçalho "
             "asaas-access-token e é o que distingue um evento legítimo de um forjado.",
    )

    def action_asaas_test_connection(self):
        self.ensure_one()
        client = self.env["asaas.config"].get_client()
        client.ping()
        ambiente = "Sandbox" if client.sandbox else "Produção"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Conexão com o Asaas (%s) confirmada.") % ambiente,
                "sticky": False,
            },
        }

    def action_asaas_sync_webhook(self):
        self.ensure_one()
        config = self.env["asaas.config"]
        token = config.get_param("asaas.webhook_token")
        if not token:
            raise UserError(_(
                "Defina o token do webhook antes de registrá-lo: sem ele o Odoo "
                "recusa todos os eventos que o Asaas enviar."
            ))
        config.get_client().sync_webhook(
            name="Odoo",
            target_url=config.webhook_url(),
            auth_token=token,
            email=self.env.user.email or self.env.company.email or "",
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Webhook registrado em %s.") % config.webhook_url(),
                "sticky": False,
            },
        }

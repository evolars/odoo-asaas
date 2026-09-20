import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from .asaas_api import AsaasAPI

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Os três campos abaixo são os mesmos do asaas_base — apontam para os mesmos
    # parâmetros de propósito, para que editar por qualquer uma das duas telas de
    # configuração grave no mesmo lugar.
    asaas_api_key = fields.Char(config_parameter="asaas.api_key")
    asaas_environment = fields.Selection(
        [("sandbox", "Sandbox"), ("production", "Produção")],
        config_parameter="asaas.environment", default="sandbox", required=True,
    )
    asaas_webhook_token = fields.Char(config_parameter="asaas.webhook_token")
    asaas_resend_api_key = fields.Char(
        config_parameter="asaas.resend_api_key",
        string="Chave de API do Resend",
        groups="base.group_system",
        help="Usada para enviar recibos e notificações por e-mail. Fica só aqui: "
             "credencial em código vaza junto com o repositório.",
    )
    asaas_receivable_journal_id = fields.Many2one("account.journal", string="Diário de recebimentos Asaas")
    asaas_default_notification_channel = fields.Selection(
        [
            ("both", "WhatsApp e E-mail"),
            ("whatsapp", "Apenas WhatsApp"),
            ("email", "Apenas E-mail"),
        ],
        config_parameter="evolars_asaas.default_notification_channel",
        default="both",
        string="Canal de Notificação Padrão",
    )

    @api.model
    def get_values(self):
        values = super().get_values()
        params = self.env["ir.config_parameter"].sudo()
        values["asaas_receivable_journal_id"] = int(params.get_param("evolars_asaas.receivable_journal_id", 0)) or False
        if not values.get("asaas_webhook_token"):
            values["asaas_webhook_token"] = secrets.token_urlsafe(36)
        return values

    def set_values(self):
        result = super().set_values()
        params = self.env["ir.config_parameter"].sudo()
        params.set_param(
            "evolars_asaas.receivable_journal_id", self.asaas_receivable_journal_id.id or ""
        )
        api_key = self.env["asaas.config"].get_param("asaas.api_key")
        token = self.env["asaas.config"].get_param("asaas.webhook_token")
        if api_key and token:
            try:
                AsaasAPI(self.env).sync_webhook(auth_token=token)
            except Exception as exc:
                _logger.warning("Falha ao sincronizar webhook automaticamente com o Asaas: %s", exc)
        return result

    def action_sync_asaas_webhook(self):
        self.ensure_one()
        params = self.env["ir.config_parameter"].sudo()
        api_key = self.asaas_api_key or self.env["asaas.config"].get_param("asaas.api_key")
        if not api_key:
            raise UserError(_("Informe a chave de API do Asaas antes de sincronizar o webhook."))

        token = self.asaas_webhook_token or self.env["asaas.config"].get_param("asaas.webhook_token")
        if not token:
            token = secrets.token_urlsafe(36)
            params.set_param("asaas.webhook_token", token)
            self.asaas_webhook_token = token

        res = AsaasAPI(self.env).sync_webhook(auth_token=token)
        events_count = len(res.get("events", []))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Webhook Asaas Sincronizado"),
                "message": _("Webhook registrado e sincronizado no Asaas com sucesso (%s eventos ativos).") % events_count,
                "type": "success",
                "sticky": False,
            },
        }

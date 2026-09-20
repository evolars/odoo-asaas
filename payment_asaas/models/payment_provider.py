from odoo import _, fields, models

from odoo.addons.asaas_base.models.asaas_client import AsaasClient
from odoo.addons.payment_asaas import const


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(
        selection_add=[("asaas", "Asaas")], ondelete={"asaas": "set default"}
    )
    asaas_api_key = fields.Char(
        string="Chave de API do Asaas",
        required_if_provider="asaas",
        groups="base.group_system",
        help="$aact_hmlg_... no Sandbox, $aact_prod_... em Produção. O ambiente "
             "segue o estado do provedor: Teste usa o Sandbox.",
    )

    def _get_supported_currencies(self):
        """O Asaas só liquida em BRL."""
        supported = super()._get_supported_currencies()
        if self.code == "asaas":
            supported = supported.filtered(lambda c: c.name in const.SUPPORTED_CURRENCIES)
        return supported

    def _get_default_payment_method_codes(self):
        default_codes = super()._get_default_payment_method_codes()
        if self.code != "asaas":
            return default_codes
        return const.DEFAULT_PAYMENT_METHOD_CODES

    def _asaas_get_client(self):
        """Cliente com a credencial deste provedor.

        O ambiente vem do `state` do próprio provedor, e não das Configurações:
        provedor em Teste tem que bater no Sandbox, sempre. Não há como um
        provedor de teste emitir cobrança de verdade por engano de configuração.
        """
        self.ensure_one()
        return AsaasClient(self.asaas_api_key, sandbox=self.state != "enabled")

    def action_asaas_test_connection(self):
        self.ensure_one()
        client = self._asaas_get_client()
        client.ping()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Conexão com o Asaas (%s) confirmada.") % (
                    "Sandbox" if client.sandbox else "Produção"
                ),
                "sticky": False,
            },
        }

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.asaas_base.models.asaas_client import AsaasClient
from odoo.addons.payment_asaas import const


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(
        selection_add=[("asaas", "Asaas")], ondelete={"asaas": "set default"}
    )
    asaas_api_key = fields.Char(
        string="Chave de API do Asaas",
        groups="base.group_system",
        help="$aact_hmlg_... no Sandbox, $aact_prod_... em Produção. O ambiente "
             "segue o estado do provedor: Teste usa o Sandbox. Vazia, vale a chave "
             "de Configurações → Asaas, se for do mesmo ambiente.",
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
        sandbox = self.state != "enabled"
        return AsaasClient(self._asaas_get_api_key(sandbox), sandbox=sandbox)

    def _asaas_get_api_key(self, sandbox):
        """Chave própria do provedor; sem ela, a de Configurações → Asaas.

        A chave costuma ser colada uma vez só, em Configurações → Asaas (é lá que
        se registra o webhook). Exigir de novo no provedor deixava o checkout sem
        pagamento com a conta pronta. A global só vale se for do mesmo ambiente:
        provedor em Teste nunca usa chave de produção, e vice-versa.
        """
        self.ensure_one()
        if self.asaas_api_key:
            return self.asaas_api_key
        config = self.env["asaas.config"]
        api_key = config.get_param("asaas.api_key")
        if api_key and config.is_sandbox() == sandbox:
            return api_key
        raise UserError(_(
            "Sem chave de API do Asaas para o ambiente %(ambiente)s: preencha a chave "
            "no provedor ou em Configurações → Asaas (com o mesmo ambiente).",
            ambiente="Sandbox" if sandbox else "Produção",
        ))

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

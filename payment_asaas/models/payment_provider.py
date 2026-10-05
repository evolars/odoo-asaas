from odoo import _, api, fields, models
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

    @api.model
    def _asaas_setup_checkout(self):
        """Prepara o checkout transparente no provedor já instalado.

        O registro do provedor é `noupdate`: sem isto, quem atualiza o módulo
        continuaria mandando o comprador para a página do Asaas e mostrando o
        texto antigo de "cobrança gerada no Asaas".
        """
        providers = self.sudo().search([("code", "=", "asaas")])
        inline_form = self.env.ref("payment_asaas.inline_form", raise_if_not_found=False)
        if inline_form:
            providers.inline_form_view_id = inline_form
        for provider in providers:
            if "Cobrança gerada no Asaas" in (provider.pending_msg or ""):
                provider.pending_msg = const.PENDING_MSG
        # A tradução pt_BR do Odoo chama o método "Card" de "Quadro".
        card = self.env.ref("payment.payment_method_card", raise_if_not_found=False)
        if card and self.env["res.lang"]._lang_get("pt_BR"):
            card_pt = card.sudo().with_context(lang="pt_BR")
            if card_pt.name == "Quadro":
                card_pt.name = "Cartão de crédito"

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

from odoo import fields, models


class ContractContract(models.Model):
    _inherit = "contract.contract"

    notification_channel = fields.Selection(
        [
            ("both", "WhatsApp e E-mail"),
            ("whatsapp", "Apenas WhatsApp"),
            ("email", "Apenas E-mail"),
            ("none", "Desabilitado"),
        ],
        string="Canal de Notificação",
        default="both",
        required=True,
    )
    asaas_subscription_ids = fields.One2many("evolars.asaas.subscription", "contract_id", string="Assinaturas Asaas")
    asaas_subscription_count = fields.Integer(compute="_compute_asaas_subscription_count")

    def _compute_asaas_subscription_count(self):
        for contract in self:
            contract.asaas_subscription_count = len(contract.asaas_subscription_ids)

    def action_view_asaas_subscriptions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Assinaturas Asaas",
            "res_model": "evolars.asaas.subscription",
            "view_mode": "tree,form",
            "domain": [("contract_id", "=", self.id)],
            "context": {
                "default_contract_id": self.id,
                "default_partner_id": self.partner_id.id,
                "default_notification_channel": self.notification_channel,
            },
        }


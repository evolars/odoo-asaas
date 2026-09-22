from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    # asaas_customer_id e asaas_notifications_disabled vêm do asaas_base:
    # o provedor de pagamento precisa dos mesmos campos.
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
        tracking=True,
    )
    asaas_payment_ids = fields.One2many("evolars.asaas.payment", "partner_id", string="Cobranças Asaas")
    asaas_subscription_ids = fields.One2many("evolars.asaas.subscription", "partner_id", string="Assinaturas Asaas")
    asaas_payment_count = fields.Integer(compute="_compute_asaas_counts")
    asaas_subscription_count = fields.Integer(compute="_compute_asaas_counts")

    def _compute_asaas_counts(self):
        for partner in self:
            partner.asaas_payment_count = len(partner.asaas_payment_ids)
            partner.asaas_subscription_count = len(partner.asaas_subscription_ids)

    def action_sync_asaas_customer(self):
        for partner in self:
            from .asaas_api import AsaasAPI
            AsaasAPI(self.env).sync_customer(partner)
            partner.message_post(body="Dados do cliente sincronizados com o Asaas.")
        return True

    def action_view_asaas_payments(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Cobranças Asaas",
            "res_model": "evolars.asaas.payment",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

    def action_view_asaas_subscriptions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Assinaturas Asaas",
            "res_model": "evolars.asaas.subscription",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

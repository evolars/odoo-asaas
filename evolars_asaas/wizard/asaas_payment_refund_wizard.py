from odoo import _, fields, models
from odoo.exceptions import UserError
from ..models.asaas_api import AsaasAPI


class AsaasPaymentRefundWizard(models.TransientModel):
    _name = "evolars.asaas.payment.refund.wizard"
    _description = "Assistente de Estorno Asaas"

    payment_id = fields.Many2one("evolars.asaas.payment", required=True, readonly=True)
    refund_type = fields.Selection(
        [("total", "Estorno Total"), ("partial", "Estorno Parcial")],
        default="total",
        required=True,
    )
    amount = fields.Monetary(currency_field="currency_id", required=True)
    currency_id = fields.Many2one(related="payment_id.currency_id")
    description = fields.Char(string="Motivo do Estorno", default="Estorno solicitado via Odoo")

    def action_confirm_refund(self):
        self.ensure_one()
        payment = self.payment_id
        if not payment.asaas_payment_id:
            raise UserError(_("Cobrança sem ID Asaas."))
        if payment.state not in ("received", "confirmed"):
            raise UserError(_("Apenas cobranças recebidas ou confirmadas podem ser estornadas."))
        
        refund_amount = self.amount if self.refund_type == "partial" else None
        if refund_amount and (refund_amount <= 0 or refund_amount > payment.amount):
            raise UserError(_("Valor de estorno inválido."))

        api = AsaasAPI(self.env)
        api.refund_payment(payment.asaas_payment_id, value=refund_amount, description=self.description)
        payment.write({"state": "refunded"})
        payment.message_post(
            body=_("Estorno realizado no Asaas (%s) no valor de R$ %0.2f. Motivo: %s")
            % (self.refund_type, refund_amount or payment.amount, self.description or _("Não informado"))
        )
        return {"type": "ir.actions.act_window_close"}

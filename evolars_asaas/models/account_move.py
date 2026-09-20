from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    asaas_transfer_id = fields.Char(
        string="ID Transferência Asaas",
        copy=False,
        readonly=True,
        index=True,
    )
    asaas_transfer_status = fields.Char(
        string="Status Transferência",
        copy=False,
        readonly=True,
    )
    asaas_end_to_end_id = fields.Char(
        string="End-to-End ID",
        copy=False,
        readonly=True,
    )
    asaas_receipt_url = fields.Char(
        string="Comprovante Asaas",
        copy=False,
        readonly=True,
    )
    can_pay_via_asaas = fields.Boolean(
        compute="_compute_can_pay_via_asaas",
        string="Pode Pagar via Asaas",
    )

    @api.depends("move_type", "state", "payment_state", "asaas_transfer_id")
    def _compute_can_pay_via_asaas(self):
        for move in self:
            move.can_pay_via_asaas = (
                move.move_type == "in_invoice"
                and move.state in ("draft", "posted")
                and move.payment_state in ("not_paid", "partial")
                and not move.asaas_transfer_id
            )

    def action_pay_via_asaas(self):
        self.ensure_one()
        if self.move_type != "in_invoice":
            raise UserError(_("Esta ação é permitida apenas para faturas de fornecedor."))
        if self.payment_state == "paid":
            raise UserError(_("Esta fatura já está quitada."))
        if self.asaas_transfer_id:
            raise UserError(
                _("Já existe uma transferência Asaas vinculada a esta fatura (%s).")
                % self.asaas_transfer_id
            )

        return {
            "type": "ir.actions.act_window",
            "name": _("Pagar via PIX (Asaas)"),
            "res_model": "evolars.asaas.bill.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_move_id": self.id,
            },
        }

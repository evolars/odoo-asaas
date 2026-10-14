import logging


from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Status de transferencia do Asaas que encerram a tentativa sem dinheiro sair.
ASAAS_TRANSFER_FAILED = ("FAILED", "CANCELLED")


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

    def _asaas_payment_journal(self):
        self.ensure_one()
        param = self.env["ir.config_parameter"].sudo().get_param("evolars_asaas.receivable_journal_id")
        journal = self.env["account.journal"].browse(int(param)).exists() if param and param.isdigit() else False
        if journal and journal.company_id == self.company_id:
            return journal
        return self.env["account.journal"].search([
            ("type", "=", "bank"),
            ("company_id", "=", self.company_id.id),
        ], limit=1)

    def _asaas_register_transfer_payment(self, amount):
        """Baixa a fatura de fornecedor. Só deve ser chamado com a transferência DONE."""
        self.ensure_one()
        if self.payment_state in ("paid", "in_payment") or self.state != "posted":
            return
        try:
            self.env["account.payment.register"].with_context(
                active_model="account.move",
                active_ids=[self.id],
            ).create({
                "payment_date": fields.Date.context_today(self),
                "amount": amount or self.amount_residual,
                "journal_id": self._asaas_payment_journal().id,
            }).action_create_payments()
        except Exception as exc:
            _logger.exception("Falha ao registrar pagamento contábil da fatura %s", self.name)
            self.message_post(body=_(
                "Transferência Asaas %(transfer)s concluída, mas houve erro no registro contábil: %(error)s",
                transfer=self.asaas_transfer_id, error=exc,
            ))

    def _asaas_apply_transfer_status(self, transfer_data, event_name=None):
        """Aplica no Odoo o status de uma transferência vinda do webhook ou da criação."""
        self.ensure_one()
        status = transfer_data.get("status")
        vals = {}
        if status:
            vals["asaas_transfer_status"] = status
        if transfer_data.get("endToEndIdentifier"):
            vals["asaas_end_to_end_id"] = transfer_data["endToEndIdentifier"]
        if transfer_data.get("transactionReceiptUrl"):
            vals["asaas_receipt_url"] = transfer_data["transactionReceiptUrl"]
        if vals:
            self.sudo().write(vals)

        if status == "DONE":
            self.sudo()._asaas_register_transfer_payment(transfer_data.get("value"))
            self.message_post(body=_("✅ Transferência PIX %s concluída no Asaas.") % transfer_data.get("id"))
        elif status in ASAAS_TRANSFER_FAILED:
            # Libera o botão para uma nova tentativa; o id antigo fica no histórico.
            self.sudo().write({"asaas_transfer_id": False})
            self.message_post(body=_(
                "⚠️ Transferência PIX %(transfer)s não foi feita (%(status)s). %(reason)s",
                transfer=transfer_data.get("id"), status=status,
                reason=transfer_data.get("failReason") or "",
            ))
        else:
            self.message_post(body=_("ℹ️ Transferência PIX %(transfer)s: %(status)s.",
                                     transfer=transfer_data.get("id"), status=status or event_name))

    def action_asaas_refresh_transfer(self):
        """Consulta o PIX no Asaas, para quando o webhook não chegou ou chegou antes da gravação."""
        from .asaas_api import AsaasAPI
        for move in self.filtered("asaas_transfer_id"):
            move._asaas_apply_transfer_status(AsaasAPI(move.env).get_transfer(move.asaas_transfer_id))

import re
import logging
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from ..models.asaas_api import AsaasAPI

_logger = logging.getLogger(__name__)

PIX_KEY_TYPES = [
    ("CPF", "CPF"),
    ("CNPJ", "CNPJ"),
    ("EMAIL", "E-mail"),
    ("PHONE", "Telefone"),
    ("EVP", "Chave Aleatória"),
]


class AsaasBillPaymentWizard(models.TransientModel):
    _name = "evolars.asaas.bill.payment.wizard"
    _description = "Assistente de Pagamento de Fatura via PIX (Asaas)"

    move_id = fields.Many2one("account.move", string="Fatura", required=True)
    partner_id = fields.Many2one(
        "res.partner",
        related="move_id.partner_id",
        string="Beneficiário / Fornecedor",
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="move_id.currency_id",
        readonly=True,
    )
    amount = fields.Monetary(
        string="Valor a Pagar",
        required=True,
    )
    pix_key = fields.Char(
        string="Chave PIX",
        required=True,
    )
    pix_key_type = fields.Selection(
        PIX_KEY_TYPES,
        string="Tipo da Chave",
        required=True,
        default="EMAIL",
    )
    asaas_balance = fields.Float(
        string="Saldo Disponível no Asaas (R$)",
        readonly=True,
    )
    description = fields.Char(
        string="Descrição (Asaas)",
        required=True,
    )
    external_reference = fields.Char(
        string="Referência Externa (Asaas)",
        required=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        move_id = self.env.context.get("default_move_id") or self.env.context.get("active_id")
        if not move_id:
            return res

        move = self.env["account.move"].browse(move_id)
        res["move_id"] = move.id
        res["amount"] = move.amount_residual or move.amount_total

        partner = move.partner_id
        if partner:
            pix_rec = self.env["res.partner.pix"].search([("partner_id", "=", partner.id)], limit=1)
            if pix_rec:
                res["pix_key"] = pix_rec.key
                type_map = {
                    "email": "EMAIL",
                    "phone": "PHONE",
                    "evp": "EVP",
                }
                if pix_rec.key_type == "cnpj_cpf":
                    digits = re.sub(r"\D", "", pix_rec.key or "")
                    res["pix_key_type"] = "CNPJ" if len(digits) > 11 else "CPF"
                else:
                    res["pix_key_type"] = type_map.get(pix_rec.key_type, "EMAIL")
            elif partner.bank_ids:
                acc_num = partner.bank_ids[0].acc_number or ""
                if "@" in acc_num:
                    res["pix_key"] = acc_num
                    res["pix_key_type"] = "EMAIL"

        doc_ref = move.name if move.name and move.name != "/" else f"BILL-{move.id}"
        partner_name = partner.name or ""
        res["description"] = f"Pagamento {move.ref or doc_ref} - {partner_name}"[:100]
        res["external_reference"] = doc_ref

        try:
            api_client = AsaasAPI(self.env)
            bal = api_client.get_balance()
            res["asaas_balance"] = bal.get("balance", 0.0)
        except Exception as exc:
            _logger.warning("Não foi possível consultar saldo do Asaas: %s", exc)
            res["asaas_balance"] = 0.0

        return res

    def action_confirm_payment(self):
        self.ensure_one()
        move = self.move_id

        if move.move_type != "in_invoice":
            raise UserError(_("Operação permitida apenas para faturas de fornecedor."))
        if move.payment_state == "paid":
            raise UserError(_("Esta fatura já está marcada como quitada."))
        if move.asaas_transfer_id:
            raise UserError(
                _("Já existe uma transferência Asaas vinculada a esta fatura (%s).")
                % move.asaas_transfer_id
            )
        if self.amount <= 0:
            raise UserError(_("O valor do pagamento deve ser superior a zero."))

        if self.asaas_balance > 0 and self.amount > self.asaas_balance:
            raise UserError(
                _("Saldo insuficiente no Asaas. Saldo disponível: R$ %.2f | Valor solicitado: R$ %.2f")
                % (self.asaas_balance, self.amount)
            )

        if move.state == "draft":
            move.action_post()

        api_client = AsaasAPI(self.env)
        try:
            transfer_res = api_client.transfer_pix(
                value=self.amount,
                pix_address_key=self.pix_key.strip(),
                pix_address_key_type=self.pix_key_type,
                description=self.description,
                external_reference=self.external_reference,
            )
        except Exception as exc:
            _logger.exception("Erro ao executar transferência PIX no Asaas para fatura %s", move.name)
            raise UserError(_("Falha ao comunicar com o Asaas: %s") % str(exc)) from exc

        transfer_id = transfer_res.get("id")
        transfer_status = transfer_res.get("status")
        end_to_end = transfer_res.get("endToEndIdentifier")
        receipt_url = transfer_res.get("transactionReceiptUrl")

        move.write({
            "asaas_transfer_id": transfer_id,
            "asaas_transfer_status": transfer_status,
            "asaas_end_to_end_id": end_to_end,
            "asaas_receipt_url": receipt_url,
        })

        # Conciliação Contábil Automática via account.payment.register
        journal = self.env["account.journal"].search([
            ("type", "=", "bank"),
            ("company_id", "=", move.company_id.id),
        ], limit=1)
        if not journal:
            journal = self.env["account.journal"].browse(6)

        try:
            pay_wizard = self.env["account.payment.register"].with_context(
                active_model="account.move",
                active_ids=[move.id],
            ).create({
                "payment_date": fields.Date.context_today(self),
                "amount": self.amount,
                "journal_id": journal.id,
            })
            pay_wizard.action_create_payments()
        except Exception as exc:
            _logger.exception("Falha ao registrar pagamento contábil da fatura %s", move.name)
            move.message_post(body=_("Transferência Asaas realizada (%s), mas houve erro no registro contábil: %s") % (transfer_id, str(exc)))


        receipt_html = f'<a href="{receipt_url}" target="_blank" class="btn btn-sm btn-link">Abrir Comprovante Asaas</a>' if receipt_url else "N/A"
        body_msg = (
            f"<p><strong>✅ Pagamento via PIX Asaas Realizado com Sucesso!</strong></p>"
            f"<ul>"
            f"<li><strong>Valor:</strong> R$ {self.amount:,.2f}</li>"
            f"<li><strong>Chave PIX:</strong> {self.pix_key} ({self.pix_key_type})</li>"
            f"<li><strong>ID Asaas:</strong> <code>{transfer_id}</code></li>"
            f"<li><strong>Status:</strong> {transfer_status}</li>"
            f"<li><strong>End-to-End ID:</strong> {end_to_end or 'Em processamento bancário'}</li>"
            f"<li><strong>Referência Externa:</strong> {self.external_reference}</li>"
            f"<li><strong>Comprovante:</strong> {receipt_html}</li>"
            f"</ul>"
        )
        move.message_post(body=body_msg, subtype_xmlid="mail.mt_comment")

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Pagamento Concluído"),
                "message": _("Transferência PIX enviada via Asaas e fatura baixada com sucesso!"),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

import re

from odoo import api, models

# Diferença de arredondamento aceita entre o valor pedido e o que o Asaas informa.
VALUE_TOLERANCE = 0.01


def _normalize_pix_key(key):
    key = (key or "").strip().lower()
    if "@" in key or re.search(r"[a-f]", key) and "-" in key:
        return key  # e-mail ou chave aleatória
    return re.sub(r"\D", "", key)  # CPF, CNPJ, telefone


class AsaasWithdrawalValidation(models.Model):
    _inherit = "asaas.withdrawal.validation"

    @api.model
    def _asaas_decide(self, operation, data, payload):
        if operation == "TRANSFER":
            return self._decide_bill_transfer(data)
        return super()._asaas_decide(operation, data, payload)

    @api.model
    def _decide_bill_transfer(self, transfer):
        """Aprova só o PIX que o próprio Odoo pediu para pagar uma conta de fornecedor."""
        Move = self.env["account.move"].sudo()
        transfer_id = transfer.get("id")
        value = float(transfer.get("value") or 0.0)

        move = Move.search([("asaas_transfer_id", "=", transfer_id)], limit=1) if transfer_id else Move
        if not move:
            reference = transfer.get("externalReference")
            pending = reference and Move.search([
                ("name", "=", reference),
                ("move_type", "=", "in_invoice"),
                ("state", "=", "posted"),
                ("asaas_transfer_id", "=", False),
            ], limit=1)
            if pending:
                # O assistente grava o id da transferência só depois que o Asaas
                # responde; se a consulta chegar antes, pede para repetir.
                return "retry", "Conta %s ainda gravando a transferência." % pending.name, pending
            return "refused", "Transferência não foi pedida pelo Odoo.", None

        if move.move_type != "in_invoice" or move.state != "posted":
            return "refused", "%s não é uma conta de fornecedor confirmada." % move.name, move
        if move.payment_state in ("paid", "in_payment", "reversed"):
            return "refused", "Conta %s já está paga." % move.name, move

        expected = move.asaas_transfer_amount or move.amount_residual
        if abs(value - expected) > VALUE_TOLERANCE or value > move.amount_residual + VALUE_TOLERANCE:
            return "refused", "Valor R$ %.2f não confere com a conta %s (R$ %.2f)." % (
                value, move.name, expected), move

        sent_key = (transfer.get("bankAccount") or {}).get("pixAddressKey") or transfer.get("pixAddressKey")
        if sent_key and move.asaas_transfer_pix_key and \
                _normalize_pix_key(sent_key) != _normalize_pix_key(move.asaas_transfer_pix_key):
            return "refused", "Chave PIX não confere com a pedida na conta %s." % move.name, move

        move.message_post(body=self.env._(
            "🔐 Saque de R$ %(value).2f aprovado pela validação do Odoo (transferência %(transfer)s).",
            value=value, transfer=transfer_id,
        ))
        return "approved", "Conta %s de %s." % (move.name, move.partner_id.display_name), move

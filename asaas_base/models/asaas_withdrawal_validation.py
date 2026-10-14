import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Tipos de operação que o Asaas submete à validação de saque.
OPERATION_TYPES = [
    ("TRANSFER", "Transferência"),
    ("BILL", "Pagamento de conta"),
    ("PIX_QR_CODE", "Pix por QR Code"),
    ("MOBILE_PHONE_RECHARGE", "Recarga de celular"),
    ("PIX_REFUND", "Estorno de Pix"),
    ("PAYMENT_SPLIT", "Split de pagamento"),
]


class AsaasWithdrawalValidation(models.Model):
    """Consulta do Asaas perguntando se um saque pode sair.

    O Asaas chama o Odoo uns cinco segundos depois de criar a operação e só a
    executa com APPROVED. Cada consulta fica gravada com a decisão e o motivo,
    para dar para auditar depois quem aprovou o quê.
    """

    _name = "asaas.withdrawal.validation"
    _description = "Validação de saque do Asaas"
    _order = "create_date desc, id desc"

    name = fields.Selection(OPERATION_TYPES, string="Operação", readonly=True)
    operation_type = fields.Char(string="Tipo no Asaas", readonly=True)
    asaas_id = fields.Char(string="ID no Asaas", index=True, readonly=True)
    value = fields.Float(string="Valor (R$)", readonly=True)
    decision = fields.Selection(
        [("approved", "Aprovado"), ("refused", "Recusado"), ("retry", "Aguardando")],
        string="Decisão", required=True, default="refused", index=True, readonly=True,
    )
    reason = fields.Char(string="Motivo", readonly=True)
    res_model = fields.Char(string="Modelo relacionado", readonly=True)
    res_id = fields.Integer(string="Registro relacionado", readonly=True)
    payload = fields.Text(readonly=True)

    @api.model
    def evaluate(self, payload):
        """Decide a consulta, grava e devolve o registro.

        Na dúvida, recusa: um saque legítimo recusado se refaz; dinheiro que saiu
        sem dever não volta.
        """
        operation = payload.get("type") or ""
        data = payload.get(self._payload_key(operation)) or {}
        try:
            decision, reason, record = self._asaas_decide(operation, data, payload)
        except Exception as error:  # noqa: BLE001 - a resposta ao Asaas não pode cair
            _logger.exception("Asaas: falha ao validar saque %s", data.get("id"))
            decision, reason, record = "refused", "Erro ao validar no Odoo: %s" % error, None
        values = {
            "operation_type": operation,
            "asaas_id": data.get("id"),
            "value": float(data.get("value") or 0.0),
            "decision": decision,
            "reason": (reason or "")[:250],
            "payload": json.dumps(payload, ensure_ascii=False, indent=2),
        }
        if operation in dict(OPERATION_TYPES):
            values["name"] = operation
        if record:
            values.update(res_model=record._name, res_id=record.id)
        return self.sudo().create(values)

    @api.model
    def _payload_key(self, operation):
        return {
            "TRANSFER": "transfer",
            "BILL": "bill",
            "PIX_QR_CODE": "pixQrCode",
            "MOBILE_PHONE_RECHARGE": "mobilePhoneRecharge",
            "PIX_REFUND": "pixRefund",
            "PAYMENT_SPLIT": "paymentSplit",
        }.get(operation, "")

    @api.model
    def _asaas_decide(self, operation, data, payload):
        """Ponto de extensão: devolve (decisão, motivo, registro relacionado).

        Quem reconhece a operação aprova; o `asaas_base` sozinho recusa tudo.
        """
        return "refused", "Operação não foi iniciada pelo Odoo.", None

    def action_open_record(self):
        self.ensure_one()
        if not self.res_model or not self.res_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": self.res_model,
            "res_id": self.res_id,
            "view_mode": "form",
        }

import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AsaasWebhookController(http.Controller):
    """Recebe os eventos do Asaas.

    `/evolars_asaas/webhook` continua atendendo: bases que já existem têm esse
    endereço registrado do lado do Asaas, e trocar a URL lá é um passo manual
    que não vale exigir de quem só atualizou o módulo.
    """

    @http.route(
        ["/asaas/webhook", "/evolars_asaas/webhook"],
        type="http", auth="public", methods=["POST"], csrf=False, save_session=False,
    )
    def receive(self, **kwargs):
        expected = (request.env["asaas.config"].sudo().get_param("asaas.webhook_token") or "").strip()
        received = (request.httprequest.headers.get("asaas-access-token") or "").strip()

        if not expected:
            _logger.warning("Asaas: evento recusado — token do webhook não configurado no Odoo.")
            return request.make_response("Forbidden", status=403)
        if not received or not hmac.compare_digest(expected, received):
            _logger.warning(
                "Asaas: evento recusado — token não confere (ip=%s)",
                request.httprequest.remote_addr,
            )
            return request.make_response("Forbidden", status=403)

        try:
            payload = json.loads(request.httprequest.get_data() or b"{}")
        except ValueError:
            _logger.warning("Asaas: corpo do evento não é JSON válido.")
            return request.make_response("Invalid event", status=400)

        request.env["asaas.webhook.event"].sudo().ingest(payload)
        # 200 mesmo quando ninguém tratou: o evento está gravado, e devolver erro
        # faria o Asaas reenviar em loop algo que não vai mudar de resultado.
        return request.make_response("OK", status=200)


class AsaasWithdrawalController(http.Controller):
    """Validação de saque: o Asaas pergunta, o Odoo responde APPROVED ou REFUSED.

    Token próprio (`asaas.withdrawal_token`), separado do webhook de eventos:
    quem tem o token deste endereço autoriza dinheiro saindo da conta.
    """

    @http.route(
        "/asaas/withdrawal",
        type="http", auth="public", methods=["POST"], csrf=False, save_session=False,
    )
    def validate(self, **kwargs):
        expected = (request.env["asaas.config"].sudo().get_param("asaas.withdrawal_token") or "").strip()
        received = (request.httprequest.headers.get("asaas-access-token") or "").strip()
        if not expected or not received or not hmac.compare_digest(expected, received):
            _logger.warning(
                "Asaas: validação de saque recusada — token ausente ou não confere (ip=%s)",
                request.httprequest.remote_addr,
            )
            return request.make_response("Forbidden", status=403)

        try:
            payload = json.loads(request.httprequest.get_data() or b"{}")
        except ValueError:
            return request.make_json_response(
                {"status": "REFUSED", "refuseReason": "Corpo inválido"})

        record = request.env["asaas.withdrawal.validation"].sudo().evaluate(payload)
        if record.decision == "retry":
            # Erro faz o Asaas repetir a consulta (até três vezes); usado quando a
            # operação é do Odoo mas ainda não terminou de ser gravada.
            return request.make_response("Retry", status=503)
        if record.decision == "approved":
            return request.make_json_response({"status": "APPROVED"})
        return request.make_json_response(
            {"status": "REFUSED", "refuseReason": record.reason or "Recusado pelo Odoo"})

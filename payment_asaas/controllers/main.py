import logging

import psycopg2

from odoo import _, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

_logger = logging.getLogger(__name__)

FINAL_STATES = ("done", "authorized", "cancel", "error")


class AsaasController(http.Controller):

    @http.route("/payment/asaas/return", type="http", auth="public", methods=["GET"], csrf=False)
    def asaas_return(self, **data):
        """Retorno do comprador vindo da página do Asaas (fluxo de redirecionamento).

        O que decide o pagamento é o webhook, não esta volta: o comprador pode fechar
        o navegador antes de ser redirecionado, e no boleto e no Pix ele volta muito
        antes de o dinheiro cair. Aqui só sincronizamos a situação para que a página
        de status mostre algo verdadeiro em vez de ficar girando.
        """
        reference = data.get("externalReference") or data.get("reference")
        if reference:
            tx = request.env["payment.transaction"].sudo().search(
                [("reference", "=", reference), ("provider_code", "=", "asaas")], limit=1,
            )
            if tx and tx.provider_reference:
                try:
                    tx._asaas_sync_from_api()
                except Exception:
                    _logger.exception("Asaas: falha ao consultar a cobrança no retorno de %s", reference)
        return request.redirect("/payment/status")

    def _get_transaction(self, reference, access_token):
        tx = request.env["payment.transaction"].sudo().search(
            [("reference", "=", reference), ("provider_code", "=", "asaas")], limit=1,
        )
        if not tx or not tx._asaas_check_access_token(access_token):
            raise ValidationError(_("Transação não encontrada."))
        return tx

    @http.route("/payment/asaas/charge", type="json", auth="public")
    def asaas_charge(self, reference, access_token, card=None, document=None):
        """Cria a cobrança do checkout transparente (Pix, boleto ou cartão).

        Erros voltam como `{"error": ...}` em vez de exceção: a exceção desfaria a
        transação inteira, e o cartão recusado precisa ficar registrado como erro.
        """
        tx = self._get_transaction(reference, access_token)
        headers = request.httprequest.headers
        remote_ip = (headers.get("X-Forwarded-For") or "").split(",")[0].strip() \
            or request.httprequest.remote_addr
        try:
            tx = tx._asaas_set_payer_document(document)
            tx._asaas_create_direct_charge(card=card, remote_ip=remote_ip)
        except (UserError, ValidationError) as error:
            # AsaasError é UserError: a mensagem já vem traduzida para o comprador.
            return {"error": error.args[0] if error.args else _("Pagamento recusado.")}
        return {"state": tx.state}

    @http.route("/payment/asaas/poll", type="json", auth="public")
    def asaas_poll(self, reference, access_token, sync=False):
        """Situação da transação para a página que mostra o Pix ou o boleto.

        Com `sync`, pergunta ao Asaas em vez de só esperar o webhook. Quando a
        transação fecha, faz o pós-processamento (confirma o pedido) na hora, sem
        esperar o cron.
        """
        tx = self._get_transaction(reference, access_token)
        if sync and tx.state not in FINAL_STATES:
            try:
                tx._asaas_sync_from_api()
            except Exception:
                _logger.warning("Asaas: falha ao consultar a cobrança %s", reference, exc_info=True)
        if tx.state in FINAL_STATES and not tx.is_post_processed:
            try:
                tx._post_process()
            except psycopg2.OperationalError:
                request.env.cr.rollback()
            except Exception:
                request.env.cr.rollback()
                _logger.exception("Asaas: falha no pós-processamento de %s", reference)
        return {"state": tx.state}

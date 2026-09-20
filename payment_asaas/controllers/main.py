import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AsaasController(http.Controller):
    """Retorno do comprador vindo da página do Asaas.

    O que decide o pagamento é o webhook, não esta volta: o comprador pode fechar
    o navegador antes de ser redirecionado, e no boleto e no Pix ele volta muito
    antes de o dinheiro cair. Aqui só sincronizamos a situação para que a página
    de status mostre algo verdadeiro em vez de ficar girando.
    """

    @http.route("/payment/asaas/return", type="http", auth="public", methods=["GET"], csrf=False)
    def asaas_return(self, **data):
        reference = data.get("externalReference") or data.get("reference")
        if reference:
            tx = request.env["payment.transaction"].sudo().search(
                [("reference", "=", reference), ("provider_code", "=", "asaas")], limit=1,
            )
            if tx and tx.provider_reference:
                try:
                    payment = tx.provider_id._asaas_get_client().get_payment(tx.provider_reference)
                    tx._handle_notification_data("asaas", payment)
                except Exception:
                    _logger.exception("Asaas: falha ao consultar a cobrança no retorno de %s", reference)
        return request.redirect("/payment/status")

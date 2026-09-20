# -*- coding: utf-8 -*-
import base64
import logging
import os
import requests

from odoo import _, fields
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Sem chave embutida: credencial em código vaza com o repositório. A chave vem
# das Configurações (asaas.resend_api_key) ou da variável de ambiente.
RESEND_BASE_URL = "https://api.resend.com"


class ResendReceiptAPI:
    """Resilient Resend integration for sending official payment receipts and notifications."""

    def __init__(self, env):
        self.env = env

    def get_api_key(self):
        params = self.env["ir.config_parameter"].sudo()
        configured = (
            params.get_param("asaas.resend_api_key")
            or params.get_param("evolars_asaas.resend_api_key")
            or params.get_param("evolars_email.resend_api_key")
        )
        if configured:
            return configured.strip()
        env_key = os.environ.get("RESEND_API_KEY")
        if env_key:
            return env_key.strip()
        raise UserError(_(
            "Chave de API do Resend não configurada. Defina em "
            "Configurações → Asaas → Notificações."
        ))

    def _headers(self):
        api_key = self.get_api_key()
        if not api_key:
            raise UserError(_("Chave de API do Resend não configurada."))
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Evolars-FinOps-Asaas/17.0",
        }

    def send_payment_receipt(self, payment):
        """Dispatches an official, branded payment receipt email to the client via Resend."""
        partner = payment.partner_id
        if not partner or not partner.email:
            _logger.warning("Partner %s has no email address. Skipping customer receipt dispatch.", getattr(partner, "name", "Desconhecido"))
            return None

        # Executive leadership CC list per Evolars global email policy
        cc_list = [
            "anliben@icloud.com",
        ]

        from_addr = "Evolars LTDA <contato@evolars.com.br>"
        subject = f"🧾 Recibo de Pagamento — {payment.name} — Evolars LTDA"

        amount_str = f"R$ {payment.amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        date_str = payment.received_date.strftime("%d/%m/%Y") if payment.received_date else fields.Date.today().strftime("%d/%m/%Y")
        billing_type_map = {
            "PIX": "PIX Instantâneo",
            "BOLETO": "Boleto Bancário",
            "TRANSFER": "Transferência Bancária",
        }
        billing_type_label = billing_type_map.get(payment.billing_type, payment.billing_type or "PIX")
        invoice_ref = payment.invoice_id.name if payment.invoice_id else "N/A"
        partner_vat = partner.vat or "Não informado"

        html_body = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }}
  .card {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}
  .header {{ background: #0f172a; padding: 32px; text-align: center; color: #ffffff; }}
  .header h1 {{ margin: 0; font-size: 24px; font-weight: 700; letter-spacing: -0.5px; }}
  .header p {{ margin: 6px 0 0; font-size: 13px; color: #94a3b8; }}
  .badge {{ display: inline-block; background: #10b981; color: #ffffff; font-size: 12px; font-weight: 600; padding: 6px 14px; border-radius: 9999px; margin-top: 16px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .content {{ padding: 32px; }}
  .lead {{ font-size: 15px; line-height: 1.6; color: #334155; margin-bottom: 20px; }}
  .receipt-box {{ background: #f1f5f9; border-radius: 8px; padding: 20px; margin-bottom: 24px; }}
  .receipt-table {{ width: 100%; border-collapse: collapse; }}
  .receipt-table td {{ padding: 8px 0; font-size: 14px; vertical-align: top; }}
  .receipt-table .label {{ color: #64748b; width: 45%; font-weight: 500; }}
  .receipt-table .value {{ color: #0f172a; font-weight: 600; text-align: right; }}
  .amount-row td {{ font-size: 18px !important; color: #0f172a !important; padding-top: 12px !important; border-top: 1px solid #cbd5e1; }}
  .footer {{ background: #f8fafc; border-top: 1px solid #e2e8f0; padding: 24px; text-align: center; font-size: 12px; color: #64748b; }}
  .footer a {{ color: #2563eb; text-decoration: none; font-weight: 500; }}
</style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h1>EVOLARS LTDA</h1>
      <p>CNPJ: 62.014.621/0001-81 • Inovação & Engenharia de Software</p>
      <div class="badge">Pagamento Confirmado</div>
    </div>
    <div class="content">
      <p class="lead">Olá, <strong>{partner.name}</strong>!</p>
      <p class="lead">Confirmamos o recebimento e a liquidação com sucesso do seu pagamento. Segue abaixo o comprovante oficial detalhado:</p>
      <div class="receipt-box">
        <table class="receipt-table">
          <tr>
            <td class="label">Identificador do Recibo</td>
            <td class="value">{payment.name}</td>
          </tr>
          <tr>
            <td class="label">Código Asaas</td>
            <td class="value">{payment.asaas_payment_id or "—"}</td>
          </tr>
          <tr>
            <td class="label">Cliente / Pagador</td>
            <td class="value">{partner.name}</td>
          </tr>
          <tr>
            <td class="label">CPF / CNPJ</td>
            <td class="value">{partner_vat}</td>
          </tr>
          <tr>
            <td class="label">Data de Recebimento</td>
            <td class="value">{date_str}</td>
          </tr>
          <tr>
            <td class="label">Forma de Pagamento</td>
            <td class="value">{billing_type_label}</td>
          </tr>
          <tr>
            <td class="label">Fatura de Referência</td>
            <td class="value">{invoice_ref}</td>
          </tr>
          <tr class="amount-row">
            <td class="label"><strong>Valor Total Liquidado</strong></td>
            <td class="value"><strong>{amount_str}</strong></td>
          </tr>
        </table>
      </div>
      <p style="font-size: 13px; color: #64748b; line-height: 1.5;">
        Este recibo foi gerado e autenticado automaticamente pela infraestrutura de FinOps da <strong>Evolars LTDA</strong> em conciliação direta com o sistema bancário.
      </p>
    </div>
    <div class="footer">
      <strong>Evolars LTDA</strong> — Engenharia de Software de Alto Impacto<br>
      <a href="https://evolars.com.br">https://evolars.com.br</a> • <a href="mailto:contato@evolars.com.br">contato@evolars.com.br</a>
    </div>
  </div>
</body>
</html>"""

        text_body = f"""EVOLARS LTDA - RECIBO DE PAGAMENTO
CNPJ: 62.014.621/0001-81

Olá, {partner.name}!
Confirmamos o recebimento do seu pagamento com sucesso.

DETALHES DO RECIBO:
- Identificador: {payment.name}
- Código Asaas: {payment.asaas_payment_id or 'N/A'}
- Cliente: {partner.name} (CNPJ/CPF: {partner_vat})
- Data: {date_str}
- Forma: {billing_type_label}
- Fatura: {invoice_ref}
- Valor Liquidado: {amount_str}

Evolars LTDA - contato@evolars.com.br - https://evolars.com.br
"""

        payload = {
            "from": from_addr,
            "to": [partner.email.strip()],
            "cc": cc_list,
            "subject": subject,
            "html": html_body,
            "text": text_body,
        }

        try:
            response = requests.post(
                f"{RESEND_BASE_URL}/emails",
                json=payload,
                headers=self._headers(),
                timeout=20,
            )
            if response.status_code in (200, 201):
                data = response.json() or {}
                _logger.info("Resend receipt email dispatched successfully: %s", data.get("id"))
                return data
            else:
                _logger.error("Failed to send Resend receipt email (status %s): %s", response.status_code, response.text)
                return None
        except Exception as e:
            _logger.exception("Error dispatching receipt email via Resend: %s", str(e))
            return None

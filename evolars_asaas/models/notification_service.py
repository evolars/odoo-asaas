import json
import logging
import os
import re
import requests
import urllib.request
import urllib.error

from odoo import _, fields
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Sem chave embutida: credencial em código vaza com o repositório. A chave vem
# das Configurações (asaas.resend_api_key) ou da variável de ambiente.
RESEND_BASE_URL = "https://api.resend.com"
EVOLUTION_BASE_URL = "https://evolution.evolars.com.br"
EVOLUTION_INSTANCE = "lydia"
FROM_EMAIL = "Evolars LTDA <contato@evolars.com.br>"
CC_LIST = [
    "anliben@icloud.com",
]


class EvolarsNotificationService:
    def __init__(self, env):
        self.env = env

    def _get_resend_api_key(self):
        params = self.env["ir.config_parameter"].sudo()
        key = (
            params.get_param("asaas.resend_api_key")
            or params.get_param("evolars_asaas.resend_api_key")
            or params.get_param("evolars_email.resend_api_key")
            or os.environ.get("RESEND_API_KEY")
        )
        return key.strip() if key else ""

    def _resend_headers(self):
        key = self._get_resend_api_key()
        if not key:
            raise UserError(_("Chave de API do Resend não configurada."))
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "Evolars-FinOps/17.0",
        }

    def _get_evolution_config(self):
        params = self.env["ir.config_parameter"].sudo()
        base_url = (
            params.get_param("evolars_lydia.evolution_api_url")
            or params.get_param("evolars_asaas.evolution_api_url")
            or os.environ.get("EVOLUTION_API_URL")
            or EVOLUTION_BASE_URL
        ).rstrip("/")
        instance = (
            params.get_param("evolars_lydia.evolution_instance")
            or params.get_param("evolars_asaas.evolution_instance")
            or os.environ.get("EVOLUTION_INSTANCE")
            or EVOLUTION_INSTANCE
        ).strip()
        api_key = (
            params.get_param("evolars_lydia.evolution_api_key")
            or params.get_param("evolars_asaas.evolution_api_key")
            or os.environ.get("EVOLUTION_API_KEY")
            or ""
        ).strip()
        return {
            "base_url": base_url,
            "instance": instance,
            "api_key": api_key,
        }

    @staticmethod
    def format_phone_number(raw_phone):
        if not raw_phone:
            return False
        digits = re.sub(r"\D", "", str(raw_phone)).lstrip("0")
        if not digits:
            return False
        if len(digits) in (10, 11):
            return f"55{digits}"
        if digits.startswith("55") and len(digits) in (12, 13):
            return digits
        if len(digits) >= 10:
            return digits
        return False

    def send_whatsapp(self, phone_number, message_body, message_type="custom", partner=False, related_model=False, related_res_id=False, company=False):
        formatted_phone = self.format_phone_number(phone_number)
        if not formatted_phone:
            return {"success": False, "error": _("Número de telefone inválido.")}

        company_id = company.id if company else self.env.company.id
        partner_id = partner.id if partner else False

        if "evolars.lydia.whatsapp.message" in self.env:
            try:
                msg_record = self.env["evolars.lydia.whatsapp.message"].sudo().send_whatsapp_message(
                    phone_number=formatted_phone,
                    message_body=message_body,
                    message_type=message_type,
                    partner_id=partner_id,
                    related_model=related_model,
                    related_res_id=related_res_id,
                    company_id=company_id,
                )
                msg_id = msg_record.id if hasattr(msg_record, "id") else msg_record
                state = getattr(msg_record, "state", "sent")
                ext_id = getattr(msg_record, "external_message_id", False)
                return {
                    "success": state == "sent",
                    "message_id": str(ext_id or msg_id),
                    "db_id": msg_id,
                    "state": state,
                }
            except Exception as exc:
                _logger.warning("Falha ao enviar via evolars.lydia.whatsapp.message: %s", exc)

        config = self._get_evolution_config()
        endpoint = f"{config['base_url']}/message/sendText/{config['instance']}"
        payload = json.dumps({"number": formatted_phone, "text": message_body}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "apikey": config["api_key"],
            "User-Agent": "Evolars-FinOps/17.0",
        }
        req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_body = resp.read().decode("utf-8")
                ext_id = False
                try:
                    data = json.loads(resp_body)
                    ext_id = data.get("key", {}).get("id") or data.get("messageId") or data.get("id")
                except Exception:
                    pass
                return {"success": resp.getcode() in (200, 201), "message_id": ext_id or "sent", "raw": resp_body}
        except Exception as exc:
            _logger.error("Erro na chamada REST Evolution API: %s", exc)
            return {"success": False, "error": str(exc)}

    def send_email_resend(self, to_email, subject, html_body, text_body, cc=None):
        if not to_email:
            return {"success": False, "error": _("Destinatário sem e-mail cadastrado.")}

        to_addrs = [to_email.strip()] if isinstance(to_email, str) else list(to_email)
        cc_addrs = list(cc or CC_LIST)

        payload = {
            "from": FROM_EMAIL,
            "to": to_addrs,
            "cc": cc_addrs,
            "subject": subject,
            "html": html_body,
            "text": text_body,
        }
        try:
            resp = requests.post(
                f"{RESEND_BASE_URL}/emails",
                json=payload,
                headers=self._resend_headers(),
                timeout=20,
            )
            if resp.status_code in (200, 201):
                data = resp.json() or {}
                return {"success": True, "id": data.get("id")}
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as exc:
            _logger.exception("Erro ao despachar e-mail via Resend: %s", exc)
            return {"success": False, "error": str(exc)}

    def notify_payment(self, payment, event="created"):
        partner = payment.partner_id
        channel = payment.notification_channel or partner.notification_channel or "both"
        if channel == "none":
            return {"whatsapp": None, "email": None}

        amount_str = f"R$ {payment.amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        due_str = payment.due_date.strftime("%d/%m/%Y") if payment.due_date else fields.Date.today().strftime("%d/%m/%Y")
        billing_labels = {"PIX": "PIX Instantâneo", "BOLETO": "Boleto Bancário", "TRANSFER": "Transferência Bancária"}
        billing_label = billing_labels.get(payment.billing_type, payment.billing_type or "PIX")

        results = {"whatsapp": None, "email": None}

        if channel in ("whatsapp", "both"):
            phone = partner.mobile or partner.phone
            wa_text = self._build_payment_whatsapp_text(payment, amount_str, due_str, billing_label, event)
            wa_res = self.send_whatsapp(
                phone_number=phone,
                message_body=wa_text,
                message_type="billing_reminder",
                partner=partner,
                related_model="evolars.asaas.payment",
                related_res_id=payment.id,
                company=payment.company_id,
            )
            results["whatsapp"] = wa_res
            if wa_res.get("success"):
                payment.sudo().write({
                    "whatsapp_sent": True,
                    "whatsapp_sent_date": fields.Datetime.now(),
                    "whatsapp_message_id": wa_res.get("message_id"),
                    "last_notification_date": fields.Datetime.now(),
                })

        if channel in ("email", "both"):
            subject, html_body, text_body = self._build_payment_email(payment, amount_str, due_str, billing_label, event)
            mail_res = self.send_email_resend(
                to_email=partner.email,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
            )
            results["email"] = mail_res
            if mail_res.get("success"):
                vals = {
                    "last_notification_date": fields.Datetime.now(),
                    "resend_email_id": mail_res.get("id"),
                }
                if event == "received":
                    vals["receipt_sent"] = True
                    vals["receipt_sent_date"] = fields.Datetime.now()
                payment.sudo().write(vals)

        return results

    def notify_subscription(self, subscription, event="activated"):
        partner = subscription.partner_id
        channel = subscription.notification_channel or partner.notification_channel or "both"
        if channel == "none":
            return {"whatsapp": None, "email": None}

        amount_str = f"R$ {subscription.amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        due_str = subscription.next_due_date.strftime("%d/%m/%Y") if subscription.next_due_date else fields.Date.today().strftime("%d/%m/%Y")
        cycle_labels = {
            "WEEKLY": "Semanal",
            "BIWEEKLY": "Quinzenal",
            "MONTHLY": "Mensal",
            "BIMONTHLY": "Bimestral",
            "QUARTERLY": "Trimestral",
            "SEMIANNUALLY": "Semestral",
            "YEARLY": "Anual",
        }
        cycle_label = cycle_labels.get(subscription.cycle, subscription.cycle or "Mensal")
        billing_labels = {"PIX": "PIX", "BOLETO": "Boleto", "TRANSFER": "Transferência"}
        billing_label = billing_labels.get(subscription.billing_type, subscription.billing_type or "PIX")

        results = {"whatsapp": None, "email": None}

        if channel in ("whatsapp", "both"):
            phone = partner.mobile or partner.phone
            wa_text = self._build_subscription_whatsapp_text(subscription, amount_str, due_str, cycle_label, billing_label, event)
            wa_res = self.send_whatsapp(
                phone_number=phone,
                message_body=wa_text,
                message_type="custom",
                partner=partner,
                related_model="evolars.asaas.subscription",
                related_res_id=subscription.id,
                company=subscription.company_id,
            )
            results["whatsapp"] = wa_res
            if wa_res.get("success"):
                subscription.sudo().write({
                    "whatsapp_sent": True,
                    "whatsapp_sent_date": fields.Datetime.now(),
                    "whatsapp_message_id": wa_res.get("message_id"),
                    "last_notification_date": fields.Datetime.now(),
                })

        if channel in ("email", "both"):
            subject, html_body, text_body = self._build_subscription_email(subscription, amount_str, due_str, cycle_label, billing_label, event)
            mail_res = self.send_email_resend(
                to_email=partner.email,
                subject=subject,
                html_body=html_body,
                text_body=text_body,
            )
            results["email"] = mail_res
            if mail_res.get("success"):
                subscription.sudo().write({
                    "email_sent": True,
                    "email_sent_date": fields.Datetime.now(),
                    "resend_email_id": mail_res.get("id"),
                    "last_notification_date": fields.Datetime.now(),
                })

        return results

    def _build_payment_whatsapp_text(self, payment, amount_str, due_str, billing_label, event):
        partner_name = payment.partner_id.name or "Cliente"
        if event == "received":
            return (
                f"✅ *PAGAMENTO CONFIRMADO — EVOLARS LTDA*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Olá, *{partner_name}*!\n\n"
                f"Confirmamos o recebimento e liquidação com sucesso do seu pagamento:\n"
                f"🧾 *Identificador:* {payment.name}\n"
                f"💵 *Valor Liquidado:* {amount_str}\n"
                f"📅 *Data:* {payment.received_date.strftime('%d/%m/%Y') if payment.received_date else due_str}\n"
                f"💳 *Forma:* {billing_label}\n\n"
                f"O recibo oficial detalhado foi enviado para o seu e-mail institucional.\n"
                f"Agradecemos pela parceria!\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 _Lydia — Assistente Corporativa Evolars_"
            )
        elif event == "overdue":
            lines = [
                f"⚠️ *LEMBRETE DE VENCIMENTO — EVOLARS LTDA*",
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"Olá, *{partner_name}*!",
                f"",
                f"Identificamos que a cobrança abaixo consta com vencimento pendente:",
                f"📄 *Cobrança:* {payment.name}",
                f"💵 *Valor:* {amount_str}",
                f"📅 *Vencimento:* {due_str}",
                f"💳 *Forma:* {billing_label}",
            ]
            if payment.invoice_url:
                lines.append(f"🔗 *Link para Pagamento:* {payment.invoice_url}")
            if payment.billing_type == "PIX" and payment.pix_payload:
                lines.extend(["", "📋 *PIX Copia e Cola:*", f"```{payment.pix_payload}```"])
            elif payment.billing_type == "BOLETO" and payment.identification_field:
                lines.extend(["", "📄 *Linha Digitável:*", f"```{payment.identification_field}```"])
            lines.extend([
                "",
                "Caso já tenha efetuado a liquidação, por favor desconsidere este aviso.",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "🤖 _Lydia — Assistente Corporativa Evolars_",
            ])
            return "\n".join(lines)
        else:
            lines = [
                f"📄 *COBRANÇA DISPONÍVEL — EVOLARS LTDA*",
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"Olá, *{partner_name}*!",
                f"",
                f"Segue a sua cobrança emitida pela *Evolars LTDA*:",
                f"📄 *Identificador:* {payment.name}",
                f"💵 *Valor:* {amount_str}",
                f"📅 *Vencimento:* {due_str}",
                f"💳 *Forma de Pagamento:* {billing_label}",
            ]
            if payment.invoice_url:
                lines.append(f"🔗 *Pagar Online:* {payment.invoice_url}")
            if payment.billing_type == "PIX" and payment.pix_payload:
                lines.extend(["", "📋 *PIX Copia e Cola:*", f"```{payment.pix_payload}```"])
            elif payment.billing_type == "BOLETO" and payment.identification_field:
                lines.extend(["", "📄 *Linha Digitável:*", f"```{payment.identification_field}```"])
            lines.extend([
                "",
                "Qualquer dúvida técnica ou financeira, estamos à disposição.",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "🤖 _Lydia — Assistente Corporativa Evolars_",
            ])
            return "\n".join(lines)

    def _build_payment_email(self, payment, amount_str, due_str, billing_label, event):
        partner = payment.partner_id
        invoice_ref = payment.invoice_id.name if payment.invoice_id else "N/A"
        partner_vat = partner.vat or "Não informado"

        if event == "received":
            subject = f"🧾 Recibo de Pagamento — {payment.name} — Evolars LTDA"
            badge = "Pagamento Confirmado"
            lead_text = "Confirmamos o recebimento e a liquidação com sucesso do seu pagamento. Segue abaixo o comprovante oficial detalhado:"
        elif event == "overdue":
            subject = f"⚠️ Cobrança Vencida — {payment.name} — Evolars LTDA"
            badge = "Cobrança Vencida"
            lead_text = "Consta em nosso sistema uma pendência de liquidação para a fatura indicada abaixo. Solicitamos a regularização conforme os dados a seguir:"
        else:
            subject = f"📄 Nova Cobrança — {payment.name} — Evolars LTDA"
            badge = "Nova Cobrança"
            lead_text = "Disponibilizamos abaixo as instruções oficiais para liquidação da cobrança de serviços de tecnologia:"

        pay_button = ""
        if payment.invoice_url:
            pay_button = f'<div style="text-align: center; margin: 28px 0;"><a href="{payment.invoice_url}" style="background-color: #0f172a; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 14px; display: inline-block;">Acessar e Pagar Fatura Online</a></div>'

        pix_box = ""
        if payment.billing_type == "PIX" and payment.pix_payload:
            pix_box = f'<div style="background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 8px; padding: 16px; margin: 16px 0; word-break: break-all; font-family: monospace; font-size: 12px; color: #334155;"><strong>PIX Copia e Cola:</strong><br>{payment.pix_payload}</div>'
        elif payment.billing_type == "BOLETO" and payment.identification_field:
            pix_box = f'<div style="background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 8px; padding: 16px; margin: 16px 0; word-break: break-all; font-family: monospace; font-size: 12px; color: #334155;"><strong>Linha Digitável:</strong><br>{payment.identification_field}</div>'

        html_body = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }}
  .card {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}
  .header {{ background: #0f172a; padding: 32px; text-align: center; color: #ffffff; }}
  .header h1 {{ margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }}
  .header p {{ margin: 6px 0 0; font-size: 13px; color: #94a3b8; }}
  .badge {{ display: inline-block; background: #2563eb; color: #ffffff; font-size: 12px; font-weight: 600; padding: 6px 14px; border-radius: 9999px; margin-top: 16px; text-transform: uppercase; letter-spacing: 0.5px; }}
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
      <div class="badge">{badge}</div>
    </div>
    <div class="content">
      <p class="lead">Olá, <strong>{partner.name}</strong>!</p>
      <p class="lead">{lead_text}</p>
      <div class="receipt-box">
        <table class="receipt-table">
          <tr>
            <td class="label">Identificador</td>
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
            <td class="label">Data de Vencimento</td>
            <td class="value">{due_str}</td>
          </tr>
          <tr>
            <td class="label">Forma de Pagamento</td>
            <td class="value">{billing_label}</td>
          </tr>
          <tr>
            <td class="label">Fatura Vinculada</td>
            <td class="value">{invoice_ref}</td>
          </tr>
          <tr class="amount-row">
            <td class="label"><strong>Valor Total</strong></td>
            <td class="value"><strong>{amount_str}</strong></td>
          </tr>
        </table>
      </div>
      {pix_box}
      {pay_button}
    </div>
    <div class="footer">
      <strong>Evolars LTDA</strong> — Engenharia de Software de Alto Impacto<br>
      <a href="https://evolars.com.br">https://evolars.com.br</a> • <a href="mailto:contato@evolars.com.br">contato@evolars.com.br</a>
    </div>
  </div>
</body>
</html>"""

        text_body = f"""EVOLARS LTDA - {badge.upper()}
CNPJ: 62.014.621/0001-81

Olá, {partner.name}!
{lead_text}

DETALHES:
- Identificador: {payment.name}
- Código Asaas: {payment.asaas_payment_id or 'N/A'}
- Cliente: {partner.name} (CNPJ/CPF: {partner_vat})
- Vencimento: {due_str}
- Forma: {billing_label}
- Fatura: {invoice_ref}
- Valor: {amount_str}
- Link: {payment.invoice_url or 'N/A'}

Evolars LTDA - contato@evolars.com.br - https://evolars.com.br
"""
        return subject, html_body, text_body

    def _build_subscription_whatsapp_text(self, subscription, amount_str, due_str, cycle_label, billing_label, event):
        partner_name = subscription.partner_id.name or "Cliente"
        return (
            f"🚀 *ASSINATURA DE SERVIÇOS — EVOLARS LTDA*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Olá, *{partner_name}*!\n\n"
            f"Sua assinatura de serviços corporativos está confirmada e ativa:\n"
            f"📋 *Plano / Contrato:* {subscription.name}\n"
            f"💵 *Valor Recorrente:* {amount_str} ({cycle_label})\n"
            f"📅 *Próximo Vencimento:* {due_str}\n"
            f"💳 *Forma de Pagamento:* {billing_label}\n\n"
            f"As faturas e cobranças periódicas serão notificadas automaticamente por este canal.\n"
            f"Estamos à disposição!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 _Lydia — Assistente Corporativa Evolars_"
        )

    def _build_subscription_email(self, subscription, amount_str, due_str, cycle_label, billing_label, event):
        partner = subscription.partner_id
        subject = f"🚀 Confirmação de Assinatura — {subscription.name} — Evolars LTDA"
        contract_name = subscription.contract_id.name if subscription.contract_id else "N/A"

        html_body = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }}
  .card {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}
  .header {{ background: #0f172a; padding: 32px; text-align: center; color: #ffffff; }}
  .header h1 {{ margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }}
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
      <div class="badge">Assinatura Ativa</div>
    </div>
    <div class="content">
      <p class="lead">Olá, <strong>{partner.name}</strong>!</p>
      <p class="lead">Confirmamos a ativação dos serviços contínuos da sua assinatura com a <strong>Evolars LTDA</strong>. Seguem os dados cadastrados:</p>
      <div class="receipt-box">
        <table class="receipt-table">
          <tr>
            <td class="label">Identificador</td>
            <td class="value">{subscription.name}</td>
          </tr>
          <tr>
            <td class="label">Contrato Base</td>
            <td class="value">{contract_name}</td>
          </tr>
          <tr>
            <td class="label">Código Asaas</td>
            <td class="value">{subscription.asaas_subscription_id or "—"}</td>
          </tr>
          <tr>
            <td class="label">Ciclo de Faturamento</td>
            <td class="value">{cycle_label}</td>
          </tr>
          <tr>
            <td class="label">Próximo Vencimento</td>
            <td class="value">{due_str}</td>
          </tr>
          <tr>
            <td class="label">Forma de Cobrança</td>
            <td class="value">{billing_label}</td>
          </tr>
          <tr class="amount-row">
            <td class="label"><strong>Valor do Ciclo</strong></td>
            <td class="value"><strong>{amount_str}</strong></td>
          </tr>
        </table>
      </div>
    </div>
    <div class="footer">
      <strong>Evolars LTDA</strong> — Engenharia de Software de Alto Impacto<br>
      <a href="https://evolars.com.br">https://evolars.com.br</a> • <a href="mailto:contato@evolars.com.br">contato@evolars.com.br</a>
    </div>
  </div>
</body>
</html>"""

        text_body = f"""EVOLARS LTDA - ASSINATURA CONFIRMADA
CNPJ: 62.014.621/0001-81

Olá, {partner.name}!
Confirmamos a ativação da sua assinatura de serviços com a Evolars LTDA.

DETALHES:
- Assinatura: {subscription.name}
- Contrato Base: {contract_name}
- Ciclo: {cycle_label}
- Próximo Vencimento: {due_str}
- Forma: {billing_label}
- Valor do Ciclo: {amount_str}

Evolars LTDA - contato@evolars.com.br - https://evolars.com.br
"""
        return subject, html_body, text_body

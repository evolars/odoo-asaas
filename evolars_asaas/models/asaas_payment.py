# -*- coding: utf-8 -*-
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .asaas_api import AsaasAPI
from .resend_api import ResendReceiptAPI
from .notification_service import EvolarsNotificationService

_logger = logging.getLogger(__name__)

PAYMENT_STATES = [
    ("draft", "Rascunho"),
    ("pending", "Pendente"),
    ("received", "Recebido"),
    ("confirmed", "Confirmado"),
    ("overdue", "Vencido"),
    ("refunded", "Estornado"),
    ("cancelled", "Cancelado"),
]


class AsaasPayment(models.Model):
    _name = "evolars.asaas.payment"
    _description = "Cobrança Asaas"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "due_date desc, id desc"

    name = fields.Char(
        required=True,
        default=lambda self: self.env["ir.sequence"].next_by_code("evolars.asaas.payment") or _("Nova cobrança"),
        copy=False,
    )
    partner_id = fields.Many2one("res.partner", required=True, tracking=True)
    invoice_id = fields.Many2one(
        "account.move",
        domain=[("move_type", "=", "out_invoice")],
        tracking=True,
        ondelete="set null",
    )
    subscription_id = fields.Many2one("evolars.asaas.subscription", ondelete="set null", index=True)
    amount = fields.Monetary(required=True, tracking=True)
    currency_id = fields.Many2one(related="company_id.currency_id", store=True)
    due_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    billing_type = fields.Selection(
        [("PIX", "PIX"), ("BOLETO", "Boleto"), ("TRANSFER", "Transferência bancária")],
        required=True,
        default="PIX",
        tracking=True,
    )
    state = fields.Selection(PAYMENT_STATES, default="draft", required=True, tracking=True, index=True)
    asaas_payment_id = fields.Char(copy=False, readonly=True, index=True)
    invoice_url = fields.Char(readonly=True, copy=False)
    bank_slip_url = fields.Char(readonly=True, copy=False)
    pix_payload = fields.Text(readonly=True, copy=False)
    pix_qrcode_image = fields.Binary(readonly=True, copy=False)
    identification_field = fields.Char(readonly=True, copy=False)
    external_reference = fields.Char(readonly=True, copy=False, index=True)
    received_date = fields.Date(readonly=True, copy=False)
    last_invoice_viewed_date = fields.Datetime(readonly=True, copy=False, string="Visualização da Fatura")
    last_bank_slip_viewed_date = fields.Datetime(readonly=True, copy=False, string="Visualização do Boleto")
    fine_value = fields.Float(digits=(16, 2))
    fine_type = fields.Selection([("FIXED", "Valor fixo (R$)"), ("PERCENTAGE", "Percentual (%)")], default="PERCENTAGE")
    interest_value = fields.Float(digits=(16, 2))
    discount_value = fields.Float(digits=(16, 2))
    discount_type = fields.Selection([("FIXED", "Valor fixo (R$)"), ("PERCENTAGE", "Percentual (%)")], default="PERCENTAGE")
    discount_due_date_limit_days = fields.Integer(default=0)
    postal_service = fields.Boolean(default=False)
    can_be_edited = fields.Boolean(compute="_compute_capabilities")
    can_be_deleted = fields.Boolean(compute="_compute_capabilities")
    can_be_refunded = fields.Boolean(compute="_compute_capabilities")
    account_payment_id = fields.Many2one("account.payment", readonly=True, copy=False)
    split_move_id = fields.Many2one("account.move", readonly=True, copy=False, string="Lançamento dos splits")
    split_line_ids = fields.One2many("evolars.asaas.split", "payment_id", string="Splits")
    webhook_event_ids = fields.One2many("evolars.asaas.webhook.event", "payment_id")
    notification_channel = fields.Selection(
        [
            ("both", "WhatsApp e E-mail"),
            ("whatsapp", "Apenas WhatsApp"),
            ("email", "Apenas E-mail"),
            ("none", "Desabilitado"),
        ],
        default="both",
        required=True,
        tracking=True,
        string="Canal de Notificação",
    )
    whatsapp_sent = fields.Boolean(default=False, readonly=True, copy=False, string="WhatsApp Enviado", tracking=True)
    whatsapp_sent_date = fields.Datetime(readonly=True, copy=False, string="Data de Envio do WhatsApp")
    whatsapp_message_id = fields.Char(readonly=True, copy=False, string="ID Mensagem WhatsApp")
    last_notification_date = fields.Datetime(readonly=True, copy=False, string="Última Notificação")
    receipt_sent = fields.Boolean(default=False, readonly=True, copy=False, string="Recibo Enviado por E-mail", tracking=True)
    receipt_sent_date = fields.Datetime(readonly=True, copy=False, string="Data de Envio do Recibo")
    resend_email_id = fields.Char(readonly=True, copy=False, string="ID do E-mail Resend")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)

    _sql_constraints = [
        ("asaas_payment_company_unique", "unique(asaas_payment_id, company_id)", "Esta cobrança Asaas já está cadastrada nesta empresa.")
    ]

    @api.depends("state", "asaas_payment_id")
    def _compute_capabilities(self):
        for payment in self:
            payment.can_be_edited = bool(payment.asaas_payment_id and payment.state in ("pending", "overdue"))
            payment.can_be_deleted = bool(payment.asaas_payment_id and payment.state in ("pending", "overdue"))
            payment.can_be_refunded = bool(payment.asaas_payment_id and payment.state in ("received", "confirmed"))

    @api.constrains("amount", "split_line_ids")
    def _check_split_total(self):
        for payment in self:
            fixed = sum(payment.split_line_ids.filtered(lambda line: line.split_type == "fixed").mapped("amount"))
            percentage = sum(payment.split_line_ids.filtered(lambda line: line.split_type == "percentage").mapped("percentage"))
            if fixed > payment.amount:
                raise ValidationError(_("A soma dos splits fixos não pode exceder a cobrança."))
            if percentage > 100:
                raise ValidationError(_("A soma dos splits percentuais não pode exceder 100%."))

    def _split_payload(self):
        self.ensure_one()
        payload = []
        for line in self.split_line_ids:
            value = {"walletId": line.wallet_id}
            if line.split_type == "fixed":
                value["fixedValue"] = line.amount
            else:
                value["percentualValue"] = line.percentage
            payload.append(value)
        return payload

    def _build_remote_payload(self):
        self.ensure_one()
        payload = {
            "billingType": self.billing_type,
            "value": self.amount,
            "dueDate": fields.Date.to_string(self.due_date),
            "description": self.name,
            "postalService": self.postal_service,
        }
        if self.fine_value > 0:
            payload["fine"] = {
                "value": self.fine_value,
                "type": self.fine_type or "PERCENTAGE",
            }
        if self.interest_value > 0:
            payload["interest"] = {
                "value": self.interest_value,
            }
        if self.discount_value > 0:
            payload["discount"] = {
                "value": self.discount_value,
                "type": self.discount_type or "PERCENTAGE",
                "dueDateLimitDays": self.discount_due_date_limit_days or 0,
            }
        splits = self._split_payload()
        if splits:
            payload["split"] = splits
        return payload

    def action_create_remote(self):
        for payment in self:
            if payment.asaas_payment_id:
                raise UserError(_("Esta cobrança já foi enviada ao Asaas."))
            customer_id = AsaasAPI(self.env).ensure_customer(payment.partner_id)
            external_reference = "odoo-payment-%s" % payment.id
            payload = payment._build_remote_payload()
            payload["customer"] = customer_id
            payload["externalReference"] = external_reference
            result = AsaasAPI(self.env).request("POST", "/payments", payload)
            payment.write({
                "asaas_payment_id": result["id"],
                "external_reference": external_reference,
                "invoice_url": result.get("invoiceUrl"),
                "bank_slip_url": result.get("bankSlipUrl"),
                "state": payment._asaas_state(result.get("status")),
            })
            if payment.billing_type == "PIX":
                payment._sync_pix_payload()
            elif payment.billing_type == "BOLETO":
                payment._sync_boleto_details()
            payment.action_send_payment_notification()
        return True


    def action_update_remote(self):
        for payment in self:
            if not payment.asaas_payment_id:
                raise UserError(_("Esta cobrança ainda não foi criada no Asaas."))
            if payment.state not in ("pending", "overdue"):
                raise UserError(_("Apenas cobranças pendentes ou vencidas podem ser atualizadas no Asaas."))
            payload = payment._build_remote_payload()
            result = AsaasAPI(self.env).update_payment(payment.asaas_payment_id, payload)
            payment.write({
                "invoice_url": result.get("invoiceUrl") or payment.invoice_url,
                "bank_slip_url": result.get("bankSlipUrl") or payment.bank_slip_url,
                "state": payment._asaas_state(result.get("status")),
            })
            if payment.billing_type == "PIX":
                payment._sync_pix_payload()
            elif payment.billing_type == "BOLETO":
                payment._sync_boleto_details()
            payment.message_post(body=_("Cobrança atualizada com sucesso no Asaas."))
        return True

    def action_delete_remote(self):
        for payment in self:
            if not payment.asaas_payment_id:
                payment.state = "cancelled"
                continue
            if payment.state not in ("pending", "overdue", "draft"):
                raise UserError(_("Cobranças já recebidas, confirmadas ou estornadas não podem ser canceladas."))
            AsaasAPI(self.env).delete_payment(payment.asaas_payment_id)
            payment.write({"state": "cancelled"})
            payment.message_post(body=_("Cobrança cancelada com sucesso no Asaas."))
        return True

    def action_sync_from_remote(self):
        for payment in self:
            if not payment.asaas_payment_id:
                raise UserError(_("Cobrança sem ID Asaas."))
            data = AsaasAPI(self.env).get_payment(payment.asaas_payment_id)
            status = data.get("status")
            values = {
                "invoice_url": data.get("invoiceUrl") or payment.invoice_url,
                "bank_slip_url": data.get("bankSlipUrl") or payment.bank_slip_url,
            }
            if data.get("value"):
                values["amount"] = data["value"]
            if data.get("dueDate"):
                values["due_date"] = data["dueDate"]
            if status:
                values["state"] = payment._asaas_state(status)
            if data.get("clientPaymentDate") or data.get("paymentDate"):
                values["received_date"] = data.get("clientPaymentDate") or data.get("paymentDate")
            payment.write(values)
            if payment.billing_type == "PIX":
                payment._sync_pix_payload()
            elif payment.billing_type == "BOLETO":
                payment._sync_boleto_details()
            if payment.state in ("received", "confirmed") and not payment.account_payment_id:
                payment._register_accounting_payment()
            payment.message_post(body=_("Dados sincronizados com o Asaas."))
        return True

    def action_receive_in_cash(self):
        for payment in self:
            if not payment.asaas_payment_id:
                raise UserError(_("Esta cobrança ainda não foi criada no Asaas."))
            if payment.state not in ("pending", "overdue"):
                raise UserError(_("Apenas cobranças pendentes ou vencidas podem ser baixadas manualmente."))
            today = fields.Date.to_string(fields.Date.context_today(self))
            AsaasAPI(self.env).receive_in_cash(payment.asaas_payment_id, payment_date=today, value=payment.amount)
            payment.write({
                "state": "received",
                "received_date": today,
            })
            payment._register_accounting_payment()
            payment.message_post(body=_("Recebimento em dinheiro confirmado no Asaas."))
        return True

    def action_resend_notification(self):
        for payment in self:
            if not payment.asaas_payment_id:
                raise UserError(_("Cobrança sem ID Asaas."))
            AsaasAPI(self.env).resend_payment_notification(payment.asaas_payment_id)
            payment.message_post(body=_("Notificação de cobrança reenviada pelo Asaas."))
        return True

    def action_open_refund_wizard(self):
        self.ensure_one()
        if not self.asaas_payment_id:
            raise UserError(_("Esta cobrança não possui ID no Asaas."))
        if self.state not in ("received", "confirmed"):
            raise UserError(_("Apenas cobranças recebidas ou confirmadas podem ser estornadas."))
        return {
            "name": _("Estornar Cobrança Asaas"),
            "type": "ir.actions.act_window",
            "res_model": "evolars.asaas.payment.refund.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_payment_id": self.id,
                "default_amount": self.amount,
            },
        }

    def unlink(self):
        for payment in self:
            if payment.asaas_payment_id and payment.state in ("pending", "overdue"):
                try:
                    AsaasAPI(self.env).delete_payment(payment.asaas_payment_id)
                except Exception as exc:
                    _logger.warning("Falha ao cancelar cobrança %s no Asaas durante unlink: %s", payment.asaas_payment_id, exc)
        return super().unlink()

    def _sync_pix_payload(self):
        for payment in self.filtered("asaas_payment_id"):
            try:
                result = AsaasAPI(self.env).get_pix_qrcode(payment.asaas_payment_id)
                values = {}
                if result.get("payload"):
                    values["pix_payload"] = result.get("payload")
                if result.get("encodedImage"):
                    values["pix_qrcode_image"] = result.get("encodedImage")
                if values:
                    payment.write(values)
            except Exception as exc:
                _logger.warning("Falha ao sincronizar PIX da cobrança %s: %s", payment.name, exc)

    def _sync_boleto_details(self):
        for payment in self.filtered("asaas_payment_id"):
            try:
                result = AsaasAPI(self.env).get_identification_field(payment.asaas_payment_id)
                if result.get("identificationField"):
                    payment.identification_field = result.get("identificationField")
            except Exception as exc:
                _logger.warning("Falha ao sincronizar linha digitável do boleto %s: %s", payment.name, exc)

    @api.model
    def _asaas_state(self, value):
        states = {
            "PENDING": "pending",
            "RECEIVED": "received",
            "CONFIRMED": "confirmed",
            "OVERDUE": "overdue",
            "REFUNDED": "refunded",
            "REFUND_REQUESTED": "refunded",
            "REFUND_IN_PROGRESS": "refunded",
            "RECEIVED_IN_CASH": "received",
            "CHARGEBACK_REQUESTED": "refunded",
            "CHARGEBACK_DISPUTE": "refunded",
            "AWAITING_CHARGEBACK_REVERSAL": "refunded",
            "DUNNING_REQUESTED": "overdue",
            "DUNNING_RECEIVED": "received",
            "AWAITING_RISK_ANALYSIS": "pending",
            "AUTHORIZED": "pending",
            "DELETED": "cancelled",
        }
        return states.get(value, "pending")

    def action_open_invoice_url(self):
        self.ensure_one()
        if not self.invoice_url:
            raise UserError(_("A cobrança ainda não possui link no Asaas."))
        return {"type": "ir.actions.act_url", "url": self.invoice_url, "target": "new"}

    @api.onchange("partner_id")
    def _onchange_partner_id_notification_channel(self):
        if self.partner_id and self.partner_id.notification_channel:
            self.notification_channel = self.partner_id.notification_channel

    def action_send_payment_notification(self):
        for payment in self:
            results = EvolarsNotificationService(payment.env).notify_payment(payment, event="created")
            details = []
            if results.get("whatsapp") and results["whatsapp"].get("success"):
                details.append("WhatsApp (Lydia)")
            if results.get("email") and results["email"].get("success"):
                details.append("E-mail (Resend)")
            if details:
                payment.message_post(body=_("Notificação de cobrança enviada via %s.") % " e ".join(details))
            elif payment.notification_channel == "none":
                payment.message_post(body=_("Notificações desabilitadas para esta cobrança."))
            else:
                payment.message_post(body=_("Tentativa de envio de notificação de cobrança concluída."))
        return True

    def action_send_receipt_notification(self):
        for payment in self:
            results = EvolarsNotificationService(payment.env).notify_payment(payment, event="received")
            details = []
            if results.get("whatsapp") and results["whatsapp"].get("success"):
                details.append("WhatsApp (Lydia)")
            if results.get("email") and results["email"].get("success"):
                details.append("E-mail (Resend)")
            if details:
                msg = _("Comprovante/Recibo enviado com sucesso via %s.") % " e ".join(details)
                payment.message_post(body=msg)
                if payment.invoice_id:
                    payment.invoice_id.message_post(body=msg)
            else:
                payment.message_post(body=_("Tentativa de envio de recibo concluída."))
        return True

    def action_send_receipt_email(self):
        return self.action_send_receipt_notification()


    def _register_accounting_payment(self):
        """Registra o pagamento contábil no diário e concilia automaticamente com a fatura correspondente."""
        for payment in self:
            if payment.account_payment_id:
                _logger.info("Cobrança %s já possui pagamento contábil registrado (ID %s). Pulando.", payment.name, payment.account_payment_id.id)
                continue
            if payment.state not in ("received", "confirmed"):
                continue

            # Auto-vínculo inteligente de fatura caso não esteja definida
            if not payment.invoice_id and payment.partner_id:
                domain = [
                    ("move_type", "=", "out_invoice"),
                    ("partner_id", "=", payment.partner_id.id),
                    ("company_id", "=", payment.company_id.id),
                    ("payment_state", "in", ["not_paid", "partial"]),
                ]
                # Busca por valor exato primeiro
                matching_invoice = self.env["account.move"].sudo().search(domain + [("amount_total", "=", payment.amount)], limit=1)
                if not matching_invoice:
                    matching_invoice = self.env["account.move"].sudo().search(domain, order="invoice_date desc, id desc", limit=1)
                if matching_invoice:
                    payment.invoice_id = matching_invoice.id

            # Auto-post de fatura em rascunho
            if payment.invoice_id and payment.invoice_id.state == "draft":
                try:
                    payment.invoice_id.action_post()
                except Exception as exc:
                    _logger.warning("Não foi possível publicar automaticamente a fatura %s: %s", payment.invoice_id.name, exc)

            params = self.env["ir.config_parameter"].sudo()
            journal_id = int(params.get_param("evolars_asaas.receivable_journal_id", 0)) or False
            journal = self.env["account.journal"].browse(journal_id) if journal_id else False
            if not journal or not journal.exists():
                # Fallback para o primeiro diário de banco ou caixa ativo da empresa
                journal = self.env["account.journal"].search([
                    ("type", "in", ["bank", "cash"]),
                    ("company_id", "=", payment.company_id.id),
                ], limit=1)

            if not journal:
                payment.message_post(body=_("Nenhum diário bancário configurado para conciliação contábil do Asaas."))
                continue

            try:
                accounting_payment = self.env["account.payment"].create({
                    "payment_type": "inbound",
                    "partner_type": "customer",
                    "partner_id": payment.partner_id.id,
                    "amount": payment.amount,
                    "currency_id": payment.currency_id.id,
                    "date": payment.received_date or fields.Date.context_today(self),
                    "journal_id": journal.id,
                    "ref": "Asaas %s" % (payment.asaas_payment_id or payment.name),
                })
                accounting_payment.action_post()
                payment.account_payment_id = accounting_payment.id

                # Conciliação com a fatura de cliente
                if payment.invoice_id and payment.invoice_id.state == "posted":
                    invoice = payment.invoice_id
                    lines_to_reconcile = (invoice.line_ids + accounting_payment.move_id.line_ids).filtered(
                        lambda line: line.account_id.account_type == "asset_receivable" and not line.reconciled
                    )
                    if lines_to_reconcile:
                        try:
                            lines_to_reconcile.reconcile()
                        except Exception as exc:
                            _logger.warning("Conciliação direta de linhas falhou (%s). Tentando via js_assign_outstanding_line.", exc)
                            receivable = accounting_payment.move_id.line_ids.filtered(
                                lambda line: line.account_id.account_type == "asset_receivable" and not line.reconciled
                            )
                            if receivable:
                                invoice.js_assign_outstanding_line(receivable[0].id)

                payment._register_split_accounting(journal.id)
            except Exception as exc:
                _logger.exception("Falha na contabilização/conciliação da cobrança Asaas %s", payment.name)
                payment.message_post(body=_("Falha na conciliação contábil automática: %s") % str(exc))

            if not payment.receipt_sent or not payment.whatsapp_sent:
                payment.action_send_receipt_notification()


    def _register_split_accounting(self, journal_id):
        for payment in self:
            if payment.split_move_id or not payment.split_line_ids:
                continue
            if any(not line.account_id for line in payment.split_line_ids):
                payment.message_post(body=_("Os splits não foram contabilizados: informe a conta de contrapartida em cada split."))
                continue
            journal = self.env["account.journal"].browse(journal_id)
            lines = []
            total = 0
            for split in payment.split_line_ids:
                amount = split.amount if split.split_type == "fixed" else payment.currency_id.round(payment.amount * split.percentage / 100)
                total += amount
                lines.append((0, 0, {"name": "Split Asaas — %s" % split.name, "account_id": split.account_id.id, "debit": amount}))
            lines.append((0, 0, {"name": "Split Asaas — %s" % payment.name, "account_id": journal.default_account_id.id, "credit": total}))
            move = self.env["account.move"].create({
                "move_type": "entry",
                "journal_id": journal.id,
                "date": payment.received_date or fields.Date.context_today(self),
                "ref": "Asaas splits %s" % payment.asaas_payment_id,
                "line_ids": lines,
            })
            move.action_post()
            payment.split_move_id = move.id


class AsaasSplit(models.Model):
    _name = "evolars.asaas.split"
    _description = "Split Asaas"
    _order = "sequence, id"

    payment_id = fields.Many2one("evolars.asaas.payment", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    wallet_id = fields.Char(required=True, string="Wallet ID")
    split_type = fields.Selection([("fixed", "Valor fixo"), ("percentage", "Percentual")], required=True, default="fixed")
    amount = fields.Monetary(currency_field="currency_id")
    percentage = fields.Float(digits=(16, 6))
    account_id = fields.Many2one("account.account", string="Conta de contrapartida", domain=[("deprecated", "=", False)])
    currency_id = fields.Many2one(related="payment_id.currency_id")

    @api.constrains("split_type", "amount", "percentage")
    def _check_value(self):
        for split in self:
            if split.split_type == "fixed" and split.amount <= 0:
                raise ValidationError(_("Informe um valor fixo positivo para o split."))
            if split.split_type == "percentage" and not 0 < split.percentage <= 100:
                raise ValidationError(_("O percentual do split deve estar entre 0 e 100."))


class AsaasWebhookEvent(models.Model):
    _name = "evolars.asaas.webhook.event"
    _description = "Evento de webhook Asaas"
    _order = "received_at desc, id desc"

    external_event_id = fields.Char(required=True, index=True, copy=False)
    event = fields.Char(required=True, index=True)
    payload = fields.Text(required=True, copy=False)
    payment_id = fields.Many2one("evolars.asaas.payment", ondelete="set null", index=True)
    subscription_id = fields.Many2one("evolars.asaas.subscription", ondelete="set null", index=True)
    received_at = fields.Datetime(default=fields.Datetime.now, required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)

    _sql_constraints = [("asaas_event_company_unique", "unique(external_event_id, company_id)", "Este evento Asaas já foi processado.")]

    @api.model
    def _parse_iso_datetime(self, value):
        if not value or not isinstance(value, str):
            return False
        cleaned = value.strip().replace("T", " ").replace("Z", "")[:19]
        return cleaned if len(cleaned) == 19 else False

    @api.model
    def _resolve_partner(self, customer_id, company_id):
        if not customer_id:
            return self.env["res.partner"].sudo().search([("is_company", "=", True)], limit=1) or self.env.user.partner_id

        partner = self.env["res.partner"].sudo().search([("asaas_customer_id", "=", customer_id)], limit=1)
        if partner:
            return partner

        try:
            client = AsaasAPI(self.env)
            cust_data = client.get_customer(customer_id)
            cpf_cnpj = cust_data.get("cpfCnpj")
            email = cust_data.get("email")
            name = cust_data.get("name")
            phone = cust_data.get("phone") or cust_data.get("mobilePhone")

            if cpf_cnpj:
                digits = "".join(c for c in cpf_cnpj if c.isalnum())
                partner = self.env["res.partner"].sudo().search([("vat", "ilike", digits)], limit=1)
                if not partner:
                    for candidate in self.env["res.partner"].sudo().search([("vat", "!=", False)]):
                        if "".join(c for c in (candidate.vat or "") if c.isalnum()) == digits:
                            partner = candidate
                            break

            if not partner and email:
                partner = self.env["res.partner"].sudo().search([("email", "=ilike", email.strip())], limit=1)

            if not partner and name:
                partner = self.env["res.partner"].sudo().search([("name", "=ilike", name.strip())], limit=1)

            if partner:
                partner.sudo().write({"asaas_customer_id": customer_id})
                return partner

            if name:
                return self.env["res.partner"].sudo().create({
                    "name": name,
                    "email": email or False,
                    "vat": cpf_cnpj or False,
                    "phone": phone or False,
                    "asaas_customer_id": customer_id,
                    "company_id": company_id,
                })
        except Exception as exc:
            _logger.warning("Falha ao resolver parceiro Asaas %s: %s", customer_id, exc)

        return self.env["res.partner"].sudo().search([("is_company", "=", True)], limit=1) or self.env.user.partner_id

    @api.model
    def ingest(self, payload):
        if not isinstance(payload, dict):
            raise ValidationError(_("Formato de payload Asaas inválido."))
        event_id = payload.get("id")
        event_name = payload.get("event")
        if not event_id or not event_name:
            raise ValidationError(_("Evento Asaas inválido: id e event são obrigatórios."))

        company = self.env.company
        existing = self.sudo().search([("external_event_id", "=", event_id), ("company_id", "=", company.id)], limit=1)
        if existing:
            return existing

        record = self.sudo().create({
            "external_event_id": event_id,
            "event": event_name,
            "payload": json.dumps(payload),
            "company_id": company.id,
        })
        self._process_payload(payload, existing_record=record)
        return record

    def action_reprocess(self):
        for record in self:
            payload = json.loads(record.payload or "{}")
            self._process_payload(payload, existing_record=record)
        return True

    @api.model
    def reprocess_unlinked_events(self):
        unlinked = self.search([("payment_id", "=", False)])
        processed_count = 0
        for record in unlinked:
            try:
                payload = json.loads(record.payload or "{}")
                self._process_payload(payload, existing_record=record)
                processed_count += 1
            except Exception as exc:
                _logger.warning("Falha ao reprocessar evento %s: %s", record.id, exc)
        return processed_count

    @api.model
    def _process_payload(self, payload, existing_record=None):
        event_name = payload.get("event")
        payment_data = payload.get("payment") or {}
        subscription_data = payload.get("subscription") or {}
        transfer_data = payload.get("transfer") or {}
        bill_data = payload.get("bill") or {}

        payment = False
        if payment_data.get("id"):
            payment = self.env["evolars.asaas.payment"].sudo().search([("asaas_payment_id", "=", payment_data["id"])], limit=1)

        if not payment and payment_data.get("externalReference"):
            ext_ref = payment_data.get("externalReference")
            payment = self.env["evolars.asaas.payment"].sudo().search([("external_reference", "=", ext_ref)], limit=1)
            if not payment and isinstance(ext_ref, str) and ext_ref.startswith("odoo-payment-"):
                try:
                    pay_id = int(ext_ref.replace("odoo-payment-", ""))
                    found_pay = self.env["evolars.asaas.payment"].sudo().browse(pay_id)
                    if found_pay.exists():
                        payment = found_pay
                except (ValueError, TypeError):
                    payment = False
            if payment and payment_data.get("id"):
                payment.sudo().write({"asaas_payment_id": payment_data["id"]})

        subscription = False
        sub_id = subscription_data.get("id") or payment_data.get("subscription")
        if sub_id:
            subscription = self.env["evolars.asaas.subscription"].sudo().search([("asaas_subscription_id", "=", sub_id)], limit=1)

        target_company_id = (subscription and subscription.company_id.id) or (payment and payment.company_id.id) or self.env.company.id

        if not payment and payment_data.get("id"):
            partner = False
            if subscription and subscription.partner_id:
                partner = subscription.partner_id
            else:
                partner = self._resolve_partner(payment_data.get("customer"), target_company_id)

            billing_type = payment_data.get("billingType") or (subscription and subscription.billing_type) or "PIX"
            amount = float(payment_data.get("value") or (subscription and subscription.amount) or 0.0)
            due_date = payment_data.get("dueDate") or fields.Date.context_today(self)
            name_label = payment_data.get("description") or (subscription and _("Cobrança %s - %s") % (subscription.name, due_date)) or (_("Cobrança Asaas %s") % payment_data.get("id"))

            payment = self.env["evolars.asaas.payment"].sudo().create({
                "name": name_label,
                "partner_id": partner.id,
                "subscription_id": subscription.id if subscription else False,
                "notification_channel": (subscription and subscription.notification_channel) or getattr(partner, "notification_channel", "both") or "both",
                "amount": amount,
                "billing_type": billing_type,
                "due_date": due_date,
                "asaas_payment_id": payment_data.get("id"),
                "invoice_url": payment_data.get("invoiceUrl"),
                "bank_slip_url": payment_data.get("bankSlipUrl"),
                "external_reference": payment_data.get("externalReference") or (subscription and ("odoo-sub-%s-%s" % (subscription.id, payment_data.get("id")))) or False,
                "company_id": target_company_id,
                "state": self.env["evolars.asaas.payment"]._asaas_state(payment_data.get("status", "PENDING")),
                "last_invoice_viewed_date": self._parse_iso_datetime(payment_data.get("lastInvoiceViewedDate")),
                "last_bank_slip_viewed_date": self._parse_iso_datetime(payment_data.get("lastBankSlipViewedDate")),
            })
            if payment.billing_type == "PIX":
                payment._sync_pix_payload()
            elif payment.billing_type == "BOLETO":
                payment._sync_boleto_details()
            if event_name == "PAYMENT_CREATED":
                payment.action_send_payment_notification()

        if existing_record:
            write_vals = {}
            if payment and existing_record.payment_id != payment:
                write_vals["payment_id"] = payment.id
            if subscription and existing_record.subscription_id != subscription:
                write_vals["subscription_id"] = subscription.id
            if write_vals:
                existing_record.sudo().write(write_vals)

        if payment:
            if event_name in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
                target_state = "received" if event_name == "PAYMENT_RECEIVED" else "confirmed"
                received_date = payment_data.get("clientPaymentDate") or payment_data.get("paymentDate") or fields.Date.context_today(self)
                payment.sudo().write({
                    "state": target_state,
                    "received_date": received_date,
                })
                payment.sudo()._register_accounting_payment()
                payment.message_post(body=_("✅ Webhook Asaas: Pagamento registrado com sucesso (%s) em %s.") % (event_name, received_date))

            elif event_name == "PAYMENT_OVERDUE":
                payment.sudo().write({"state": "overdue"})
                msg_body = _("⚠️ Alerta de Inadimplência Asaas: Cobrança %s no valor de R$ %0.2f com vencimento em %s está vencida.") % (payment.name, payment.amount, payment.due_date)
                payment.message_post(body=msg_body)
                if payment.invoice_id:
                    payment.invoice_id.message_post(body=msg_body)
                EvolarsNotificationService(self.env).notify_payment(payment, event="overdue")

                try:
                    activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
                    model_record = self.env["ir.model"].sudo().search([("model", "=", "evolars.asaas.payment")], limit=1)
                    if activity_type and model_record:
                        self.env["mail.activity"].sudo().create({
                            "activity_type_id": activity_type.id,
                            "note": msg_body,
                            "res_id": payment.id,
                            "res_model_id": model_record.id,
                            "user_id": self.env.user.id,
                            "date_deadline": fields.Date.context_today(self),
                            "summary": _("Cobrança Asaas Vencida"),
                        })
                except Exception as exc:
                    _logger.warning("Não foi possível criar atividade de cobrança vencida: %s", exc)

            elif event_name == "PAYMENT_CHECKOUT_VIEWED":
                viewed_at = self._parse_iso_datetime(payment_data.get("lastInvoiceViewedDate")) or fields.Datetime.now()
                payment.sudo().write({"last_invoice_viewed_date": viewed_at})
                payment.message_post(body=_("👁️ Fatura/Checkout visualizado pelo cliente no Asaas em %s.") % viewed_at)

            elif event_name == "PAYMENT_BANK_SLIP_VIEWED":
                viewed_at = self._parse_iso_datetime(payment_data.get("lastBankSlipViewedDate")) or fields.Datetime.now()
                payment.sudo().write({"last_bank_slip_viewed_date": viewed_at})
                payment.message_post(body=_("👁️ Boleto bancário visualizado pelo cliente no Asaas em %s.") % viewed_at)

            elif event_name == "PAYMENT_AUTHORIZED":
                payment.message_post(body=_("💳 Pagamento pré-autorizado no cartão de crédito."))

            elif event_name == "PAYMENT_AWAITING_RISK_ANALYSIS":
                payment.message_post(body=_("🛡️ Pagamento em análise de risco antifraude pelo Asaas."))

            elif event_name == "PAYMENT_APPROVED_BY_RISK_ANALYSIS":
                payment.message_post(body=_("🛡️ Pagamento aprovado na análise de risco do Asaas."))

            elif event_name == "PAYMENT_REPROVED_BY_RISK_ANALYSIS":
                payment.message_post(body=_("⚠️ Pagamento reprovado na análise de risco do Asaas."))

            elif event_name == "PAYMENT_CREDIT_CARD_CAPTURE_REFUSED":
                payment.message_post(body=_("❌ Captura no cartão de crédito recusada pela operadora."))

            elif event_name == "PAYMENT_CHARGEBACK_REQUESTED":
                payment.sudo().write({"state": "refunded"})
                msg_body = _("🚨 Alerta: Notificação de Chargeback/Contestação recebida do Asaas!")
                payment.message_post(body=msg_body)
                try:
                    activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
                    model_record = self.env["ir.model"].sudo().search([("model", "=", "evolars.asaas.payment")], limit=1)
                    if activity_type and model_record:
                        self.env["mail.activity"].sudo().create({
                            "activity_type_id": activity_type.id,
                            "note": msg_body,
                            "res_id": payment.id,
                            "res_model_id": model_record.id,
                            "user_id": self.env.user.id,
                            "date_deadline": fields.Date.context_today(self),
                            "summary": _("Contestação de Cobrança (Chargeback)"),
                        })
                except Exception as exc:
                    _logger.warning("Não foi possível criar atividade de chargeback: %s", exc)

            elif event_name == "PAYMENT_CHARGEBACK_DISPUTE":
                payment.message_post(body=_("⚖️ Disputa de contestação (chargeback) aberta no Asaas."))

            elif event_name == "PAYMENT_AWAITING_CHARGEBACK_REVERSAL":
                payment.message_post(body=_("🔄 Aguardando reversão do estorno de chargeback no Asaas."))

            elif event_name in ("PAYMENT_REFUNDED", "PAYMENT_REFUND_REQUESTED", "PAYMENT_REFUND_IN_PROGRESS"):
                payment.sudo().write({"state": "refunded"})
                payment.message_post(body=_("ℹ️ Webhook Asaas: Estorno do pagamento (%s).") % event_name)

            elif event_name == "PAYMENT_REFUND_DENIED":
                payment.message_post(body=_("❌ Solicitação de estorno recusada pelo Asaas."))

            elif event_name == "PAYMENT_PARTIALLY_REFUNDED":
                payment.message_post(body=_("ℹ️ Webhook Asaas: Pagamento estornado parcialmente."))

            elif event_name == "PAYMENT_DELETED":
                payment.sudo().write({"state": "cancelled"})
                payment.message_post(body=_("❌ Webhook Asaas: Cobrança cancelada/excluída no Asaas."))

            elif event_name == "PAYMENT_ANTICIPATED":
                payment.message_post(body=_("⚡ Pagamento antecipado no Asaas."))

            elif event_name == "PAYMENT_BANK_SLIP_CANCELLED":
                payment.message_post(body=_("❌ Boleto bancário cancelado no Asaas."))

            elif event_name == "PAYMENT_DUNNING_REQUESTED":
                payment.sudo().write({"state": "overdue"})
                payment.message_post(body=_("📢 Cobrança enviada para negativação/recuperação judicial."))

            elif event_name == "PAYMENT_DUNNING_RECEIVED":
                payment.sudo().write({
                    "state": "received",
                    "received_date": payment_data.get("clientPaymentDate") or fields.Date.context_today(self),
                })
                payment.sudo()._register_accounting_payment()
                payment.message_post(body=_("💵 Cobrança recuperada após negativação (Dunning)."))

            elif event_name == "PAYMENT_SPLIT_DONE":
                payment.message_post(body=_("💰 Splits da cobrança processados com sucesso no Asaas."))

            elif event_name == "PAYMENT_SPLIT_CANCELLED":
                payment.message_post(body=_("⚠️ Splits da cobrança cancelados no Asaas."))

            elif event_name == "PAYMENT_SPLIT_DIVERGENCE_BLOCK":
                payment.message_post(body=_("🛑 Split da cobrança bloqueado por divergência no Asaas."))

            elif event_name == "PAYMENT_RECEIVED_IN_CASH_UNDONE":
                payment.sudo().write({"state": "pending"})
                payment.message_post(body=_("⚠️ Recebimento em dinheiro desfeito no Asaas."))

            elif event_name in ("PAYMENT_CREATED", "PAYMENT_UPDATED", "PAYMENT_RESTORED"):
                status = payment_data.get("status")
                if status:
                    payment.sudo().write({"state": payment._asaas_state(status)})
                if payment_data.get("invoiceUrl"):
                    payment.sudo().write({"invoice_url": payment_data["invoiceUrl"]})
                if payment_data.get("bankSlipUrl"):
                    payment.sudo().write({"bank_slip_url": payment_data["bankSlipUrl"]})

        if subscription:
            subscription.sudo()._apply_webhook(subscription_data, event_name)

        if transfer_data.get("id"):
            move = self.env["account.move"].sudo().search([("asaas_transfer_id", "=", transfer_data["id"])], limit=1)
            if move:
                t_status = transfer_data.get("status")
                if t_status:
                    move.sudo().write({"asaas_transfer_status": t_status})
                move.message_post(body=_("ℹ️ Webhook Asaas: Transferência PIX %s atualizada para status %s.") % (transfer_data["id"], t_status or event_name))

        if bill_data.get("id") or bill_data.get("externalReference"):
            ext_ref = bill_data.get("externalReference")
            domain = [("ref", "=", ext_ref)] if ext_ref else [("name", "=", bill_data.get("id"))]
            move = self.env["account.move"].sudo().search(domain, limit=1)
            if move:
                if event_name == "BILL_PAID":
                    move.message_post(body=_("✅ Pagamento de fatura de fornecedor liquidado no Asaas (%s).") % (bill_data.get("id") or ext_ref))
                elif event_name in ("BILL_FAILED", "BILL_CANCELLED"):
                    move.message_post(body=_("⚠️ Pagamento de fatura Asaas (%s) falhou ou foi cancelado (%s).") % (bill_data.get("id") or ext_ref, event_name))

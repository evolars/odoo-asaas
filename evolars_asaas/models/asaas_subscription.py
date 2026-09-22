from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .asaas_api import AsaasAPI
from .notification_service import EvolarsNotificationService


class AsaasSubscription(models.Model):
    _name = "evolars.asaas.subscription"
    _description = "Assinatura Asaas"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "next_due_date desc, id desc"

    name = fields.Char(required=True, tracking=True)
    partner_id = fields.Many2one("res.partner", required=True, tracking=True)
    contract_id = fields.Many2one("contract.contract", required=True, ondelete="cascade", index=True)
    amount = fields.Monetary(required=True, tracking=True)
    currency_id = fields.Many2one(related="company_id.currency_id", store=True)
    billing_type = fields.Selection([("PIX", "PIX"), ("BOLETO", "Boleto"), ("TRANSFER", "Transferência bancária")], required=True, default="PIX")
    cycle = fields.Selection([("WEEKLY", "Semanal"), ("BIWEEKLY", "Quinzenal"), ("MONTHLY", "Mensal"), ("BIMONTHLY", "Bimestral"), ("QUARTERLY", "Trimestral"), ("SEMIANNUALLY", "Semestral"), ("YEARLY", "Anual")], required=True, default="MONTHLY")
    next_due_date = fields.Date(required=True, default=fields.Date.context_today)
    term_type = fields.Selection([("indeterminate", "Prazo indeterminado"), ("fixed", "Prazo determinado")], required=True, default="indeterminate", tracking=True)
    end_date = fields.Date()
    max_payments = fields.Integer()
    state = fields.Selection([("draft", "Rascunho"), ("active", "Ativa"), ("inactive", "Inativa"), ("expired", "Encerrada")], default="draft", required=True, tracking=True)
    asaas_subscription_id = fields.Char(readonly=True, copy=False, index=True)
    split_line_ids = fields.One2many("evolars.asaas.subscription.split", "subscription_id", string="Splits")
    fine_value = fields.Float(digits=(16, 2))
    fine_type = fields.Selection([("FIXED", "Valor fixo (R$)"), ("PERCENTAGE", "Percentual (%)")], default="PERCENTAGE")
    interest_value = fields.Float(digits=(16, 2))
    discount_value = fields.Float(digits=(16, 2))
    discount_type = fields.Selection([("FIXED", "Valor fixo (R$)"), ("PERCENTAGE", "Percentual (%)")], default="PERCENTAGE")
    discount_due_date_limit_days = fields.Integer(default=0)
    update_pending_payments = fields.Boolean(default=True)
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
    email_sent = fields.Boolean(default=False, readonly=True, copy=False, string="E-mail Enviado", tracking=True)
    email_sent_date = fields.Datetime(readonly=True, copy=False, string="Data de Envio do E-mail")
    resend_email_id = fields.Char(readonly=True, copy=False, string="ID do E-mail Resend")
    last_notification_date = fields.Datetime(readonly=True, copy=False, string="Última Notificação")
    payment_ids = fields.One2many("evolars.asaas.payment", "subscription_id", string="Cobranças")
    payment_count = fields.Integer(compute="_compute_payment_count")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)


    _asaas_subscription_company_unique = models.Constraint(
        'unique(asaas_subscription_id, company_id)',
        "Esta assinatura Asaas já está cadastrada nesta empresa.",
    )

    def _compute_payment_count(self):
        for subscription in self:
            subscription.payment_count = len(subscription.payment_ids)

    @api.onchange("partner_id")
    def _onchange_partner_id_notification_channel(self):
        if self.partner_id and self.partner_id.notification_channel:
            self.notification_channel = self.partner_id.notification_channel

    def action_send_subscription_notification(self):
        for subscription in self:
            results = EvolarsNotificationService(subscription.env).notify_subscription(subscription, event="activated")
            details = []
            if results.get("whatsapp") and results["whatsapp"].get("success"):
                details.append("WhatsApp (Lydia)")
            if results.get("email") and results["email"].get("success"):
                details.append("E-mail (Resend)")
            if details:
                subscription.message_post(body=_("Notificação da assinatura enviada via %s.") % " e ".join(details))
            elif subscription.notification_channel == "none":
                subscription.message_post(body=_("Notificações desabilitadas para esta assinatura."))
            else:
                subscription.message_post(body=_("Tentativa de envio de notificação da assinatura concluída."))
        return True

    def action_view_payments(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Cobranças da Assinatura"),
            "res_model": "evolars.asaas.payment",
            "view_mode": "list,form",
            "domain": [("subscription_id", "=", self.id)],
            "context": {
                "default_subscription_id": self.id,
                "default_partner_id": self.partner_id.id,
                "default_notification_channel": self.notification_channel,
            },
        }


    @api.constrains("term_type", "end_date", "max_payments")
    def _check_term(self):
        for subscription in self:
            if subscription.term_type == "fixed" and not (subscription.end_date or subscription.max_payments):
                raise ValidationError(_("Informe a data final ou a quantidade máxima de cobranças para o prazo determinado."))

    @api.constrains("amount", "split_line_ids")
    def _check_split_total(self):
        for subscription in self:
            fixed = sum(subscription.split_line_ids.filtered(lambda line: line.split_type == "fixed").mapped("amount"))
            percentage = sum(subscription.split_line_ids.filtered(lambda line: line.split_type == "percentage").mapped("percentage"))
            if fixed > subscription.amount:
                raise ValidationError(_("A soma dos splits fixos não pode exceder a assinatura."))
            if percentage > 100:
                raise ValidationError(_("A soma dos splits percentuais não pode exceder 100%."))

    def _build_remote_payload(self):
        self.ensure_one()
        payload = {
            "billingType": self.billing_type,
            "value": self.amount,
            "nextDueDate": fields.Date.to_string(self.next_due_date),
            "cycle": self.cycle,
            "description": self.name,
            "postalService": False,
        }
        if self.end_date:
            payload["endDate"] = fields.Date.to_string(self.end_date)
        if self.max_payments:
            payload["maxPayments"] = self.max_payments
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
        split = self._split_payload()
        if split:
            payload["split"] = split
        return payload

    def action_create_remote(self):
        for subscription in self:
            if subscription.asaas_subscription_id:
                raise UserError(_("Esta assinatura já foi enviada ao Asaas."))
            customer_id = AsaasAPI(self.env).ensure_customer(subscription.partner_id)
            payload = subscription._build_remote_payload()
            payload["customer"] = customer_id
            payload["externalReference"] = "odoo-subscription-%s" % subscription.id
            result = AsaasAPI(self.env).request("POST", "/subscriptions", payload)
            subscription.write({"asaas_subscription_id": result["id"], "state": "active", "next_due_date": result.get("nextDueDate") or subscription.next_due_date})
            subscription.action_send_subscription_notification()
        return True


    def action_update_remote(self):
        for subscription in self:
            if not subscription.asaas_subscription_id:
                raise UserError(_("Esta assinatura ainda não foi criada no Asaas."))
            payload = subscription._build_remote_payload()
            payload["updatePendingPayments"] = subscription.update_pending_payments
            result = AsaasAPI(self.env).update_subscription(subscription.asaas_subscription_id, payload)
            subscription.write({
                "next_due_date": result.get("nextDueDate") or subscription.next_due_date,
            })
            subscription.message_post(body=_("Assinatura atualizada com sucesso no Asaas."))
        return True

    def action_inactivate_remote(self):
        for subscription in self:
            if not subscription.asaas_subscription_id:
                subscription.state = "inactive"
                continue
            AsaasAPI(self.env).update_subscription(subscription.asaas_subscription_id, {"status": "INACTIVE"})
            subscription.state = "inactive"
            subscription.message_post(body=_("Assinatura pausada no Asaas."))
        return True

    def action_activate_remote(self):
        for subscription in self:
            if not subscription.asaas_subscription_id:
                subscription.state = "active"
                continue
            AsaasAPI(self.env).update_subscription(subscription.asaas_subscription_id, {"status": "ACTIVE"})
            subscription.state = "active"
            subscription.message_post(body=_("Assinatura reativada no Asaas."))
        return True

    def action_delete_remote(self):
        for subscription in self:
            if not subscription.asaas_subscription_id:
                subscription.state = "expired"
                continue
            AsaasAPI(self.env).delete_subscription(subscription.asaas_subscription_id)
            subscription.state = "expired"
            subscription.message_post(body=_("Assinatura cancelada no Asaas."))
        return True

    def unlink(self):
        for subscription in self:
            if subscription.asaas_subscription_id and subscription.state == "active":
                try:
                    AsaasAPI(self.env).delete_subscription(subscription.asaas_subscription_id)
                except Exception as exc:
                    pass
        return super().unlink()

    def action_sync_from_remote(self):
        for subscription in self:
            if not subscription.asaas_subscription_id:
                raise UserError(_("Assinatura sem ID Asaas."))
            data = AsaasAPI(self.env).get_subscription(subscription.asaas_subscription_id)
            values = {}
            if data.get("nextDueDate"):
                values["next_due_date"] = data["nextDueDate"]
            if data.get("value"):
                values["amount"] = data["value"]
            if data.get("cycle"):
                values["cycle"] = data["cycle"]
            if data.get("billingType"):
                values["billing_type"] = data["billingType"]
            status_map = {"ACTIVE": "active", "INACTIVE": "inactive", "EXPIRED": "expired"}
            if data.get("status") in status_map:
                values["state"] = status_map[data["status"]]
            if values:
                subscription.write(values)
            subscription.message_post(body=_("Dados da assinatura sincronizados com o Asaas."))
        return True

    def action_sync_payments(self):
        for subscription in self:
            if not subscription.asaas_subscription_id:
                raise UserError(_("Assinatura sem ID Asaas."))
            result = AsaasAPI(self.env).get_subscription_payments(subscription.asaas_subscription_id)
            payments_data = result.get("data") or []
            for item in payments_data:
                pay_id = item.get("id")
                if not pay_id:
                    continue
                existing = self.env["evolars.asaas.payment"].search([("asaas_payment_id", "=", pay_id)], limit=1)
                if not existing:
                    created_pay = self.env["evolars.asaas.payment"].create({
                        "name": _("Cobrança %s - %s") % (subscription.name, item.get("dueDate")),
                        "partner_id": subscription.partner_id.id,
                        "subscription_id": subscription.id,
                        "notification_channel": subscription.notification_channel or "both",
                        "amount": item.get("value") or subscription.amount,
                        "billing_type": item.get("billingType") or subscription.billing_type or "PIX",
                        "due_date": item.get("dueDate") or fields.Date.context_today(self),
                        "asaas_payment_id": pay_id,
                        "invoice_url": item.get("invoiceUrl"),
                        "bank_slip_url": item.get("bankSlipUrl"),
                        "external_reference": item.get("externalReference") or "odoo-sub-%s-%s" % (subscription.id, pay_id),
                        "company_id": subscription.company_id.id,
                        "state": self.env["evolars.asaas.payment"]._asaas_state(item.get("status")),
                    })
                    if created_pay.billing_type == "PIX":
                        created_pay._sync_pix_payload()
                    elif created_pay.billing_type == "BOLETO":
                        created_pay._sync_boleto_details()
                    created_pay.action_send_payment_notification()
                else:
                    existing.write({
                        "invoice_url": item.get("invoiceUrl") or existing.invoice_url,
                        "bank_slip_url": item.get("bankSlipUrl") or existing.bank_slip_url,
                        "state": self.env["evolars.asaas.payment"]._asaas_state(item.get("status")),
                    })
            subscription.message_post(body=_("%s cobranças sincronizadas com o Asaas.") % len(payments_data))
        return True

    def _split_payload(self):
        self.ensure_one()
        return [line._payload() for line in self.split_line_ids]

    def _apply_webhook(self, payload, event):
        mapping = {"ACTIVE": "active", "INACTIVE": "inactive", "EXPIRED": "expired"}
        values = {"state": mapping.get(payload.get("status"), self.state)}
        if payload.get("nextDueDate"):
            values["next_due_date"] = payload["nextDueDate"]
        self.write(values)
        if event == "SUBSCRIPTION_CREATED" and not self.whatsapp_sent and not self.email_sent:
            self.action_send_subscription_notification()



class AsaasSubscriptionSplit(models.Model):
    _name = "evolars.asaas.subscription.split"
    _description = "Split da assinatura Asaas"
    _order = "sequence, id"

    subscription_id = fields.Many2one("evolars.asaas.subscription", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    wallet_id = fields.Char(required=True, string="Wallet ID")
    split_type = fields.Selection([("fixed", "Valor fixo"), ("percentage", "Percentual")], required=True, default="fixed")
    amount = fields.Monetary(currency_field="currency_id")
    percentage = fields.Float(digits=(16, 6))
    currency_id = fields.Many2one(related="subscription_id.currency_id")

    def _payload(self):
        self.ensure_one()
        result = {"walletId": self.wallet_id}
        result["fixedValue" if self.split_type == "fixed" else "percentualValue"] = self.amount if self.split_type == "fixed" else self.percentage
        return result

    @api.constrains("split_type", "amount", "percentage")
    def _check_value(self):
        for split in self:
            if split.split_type == "fixed" and split.amount <= 0:
                raise ValidationError(_("Informe um valor fixo positivo para o split."))
            if split.split_type == "percentage" and not 0 < split.percentage <= 100:
                raise ValidationError(_("O percentual do split deve estar entre 0 e 100."))

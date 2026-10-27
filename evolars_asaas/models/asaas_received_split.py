import logging
from datetime import timedelta

from odoo import Command, _, api, fields, models

_logger = logging.getLogger(__name__)

# O Asaas não avisa por webhook a conta que recebe o split: a comissão só aparece
# na listagem de splits recebidos e no extrato (INTERNAL_TRANSFER_CREDIT).
RECEIVED_SPLIT_SINCE_PARAM = "evolars_asaas.received_split_since"
# Releitura de alguns dias antes do último crédito, para splits que o Asaas
# confirma com data de crédito retroativa.
RECEIVED_SPLIT_OVERLAP_DAYS = 7


class AccountMove(models.Model):
    _inherit = "account.move"

    asaas_split_id = fields.Char(
        string="Split recebido (Asaas)",
        copy=False,
        readonly=True,
        index=True,
        help="Split que outra conta Asaas repassou para esta; a fatura é criada já paga.",
    )

    _asaas_split_company_unique = models.Constraint(
        "unique(asaas_split_id, company_id)",
        "Este split do Asaas já tem fatura nesta empresa.",
    )

    @api.model
    def _cron_asaas_sync_received_splits(self):
        self.asaas_sync_received_splits()

    @api.model
    def asaas_sync_received_splits(self, since=None, limit=200):
        """Cria uma fatura de cliente, já paga no diário do Asaas, para cada split recebido.

        Só entra o que caiu na conta (DONE); o split pendente vira fatura no dia do
        crédito. Split estornado depois de faturado ganha nota de crédito. Só são
        lidos os splits de contas Asaas ligadas a um contato (`asaas_account_id`).
        Devolve quantos splits ficaram para a próxima rodada por causa de `limit`.
        """
        partners = self.env["res.partner"].sudo().search([("asaas_account_id", "!=", False)])
        if not partners:
            return 0
        partner_by_account = {partner.asaas_account_id.strip(): partner for partner in partners}

        params = self.env["ir.config_parameter"].sudo()
        if since is None:
            last_credit = params.get_param(RECEIVED_SPLIT_SINCE_PARAM)
            if last_credit:
                since = fields.Date.to_string(
                    fields.Date.from_string(last_credit) - timedelta(days=RECEIVED_SPLIT_OVERLAP_DAYS)
                )
        query = {"creditDate[ge]": since} if since else {}

        client = self.env["asaas.config"].get_client()
        splits = []
        offset = 0
        while True:
            page = client.request("GET", "/payments/splits/received", params=dict(query, limit=100, offset=offset))
            splits += page.get("data") or []
            if not page.get("hasMore"):
                break
            offset += 100

        splits = [split for split in splits if split.get("originAccountId") in partner_by_account]
        invoices = self.sudo().search([("asaas_split_id", "in", [split["id"] for split in splits])])
        invoice_by_split = {invoice.asaas_split_id: invoice for invoice in invoices}
        todo = [
            split for split in splits
            if (split["status"] == "DONE" and split["id"] not in invoice_by_split)
            or (
                split["status"] == "REFUNDED"
                and split["id"] in invoice_by_split
                and not invoice_by_split[split["id"]].reversal_move_ids
            )
        ]
        todo.sort(key=lambda split: split.get("creditDate") or "")

        journal = self._asaas_received_split_journal()
        product = self.env.ref("evolars_asaas.product_asaas_received_split")
        for split in todo[:limit]:
            try:
                with self.env.cr.savepoint():
                    if split["status"] == "DONE":
                        partner = partner_by_account[split["originAccountId"]]
                        self._asaas_invoice_received_split(split, partner, product, journal)
                    else:
                        self._asaas_refund_received_split(invoice_by_split[split["id"]], journal)
            except Exception:
                _logger.exception("Asaas: falha ao faturar o split recebido %s", split["id"])

        remaining = max(len(todo) - limit, 0)
        credit_dates = [split["creditDate"] for split in splits if split["status"] == "DONE" and split.get("creditDate")]
        if credit_dates and not remaining:
            params.set_param(RECEIVED_SPLIT_SINCE_PARAM, max(credit_dates))
        return remaining

    @api.model
    def _asaas_received_split_journal(self):
        journal_id = int(self.env["ir.config_parameter"].sudo().get_param("evolars_asaas.receivable_journal_id", 0))
        journal = self.env["account.journal"].browse(journal_id).exists()
        return journal or self.env["account.journal"].search([
            ("type", "in", ("bank", "cash")),
            ("company_id", "=", self.env.company.id),
        ], limit=1)

    @api.model
    def _asaas_invoice_received_split(self, split, partner, product, journal):
        payment = split.get("payment") or {}
        credit_date = split["creditDate"]
        if split.get("percentualValue"):
            label = _("Comissão de %(rate)s%% sobre a fatura Asaas nr. %(number)s") % {
                "rate": ("%g" % split["percentualValue"]).replace(".", ","),
                "number": payment.get("invoiceNumber"),
            }
        else:
            label = _("Comissão sobre a fatura Asaas nr. %s") % payment.get("invoiceNumber")
        if payment.get("externalReference"):
            label = "%s (%s)" % (label, payment["externalReference"])

        invoice = self.create({
            "move_type": "out_invoice",
            "partner_id": partner.id,
            "invoice_date": credit_date,
            "date": credit_date,
            "ref": _("Split Asaas — fatura nr. %s") % payment.get("invoiceNumber"),
            # a comissão não é cobrada em conta bancária; com a conta da empresa
            # na fatura, o 19 exige que ela esteja marcada como confiável
            "partner_bank_id": False,
            "asaas_split_id": split["id"],
            "invoice_line_ids": [Command.create({
                "product_id": product.id,
                "name": label,
                "quantity": 1,
                "price_unit": split["totalValue"],
                "tax_ids": [Command.clear()],
            })],
        })
        invoice.action_post()
        self._asaas_pay_received_split(invoice, journal, credit_date)
        return invoice

    @api.model
    def _asaas_refund_received_split(self, invoice, journal):
        today = fields.Date.context_today(self)
        refund = invoice._reverse_moves([{
            "ref": _("Estorno do split Asaas %s") % invoice.asaas_split_id,
            "invoice_date": today,
            "date": today,
            "partner_bank_id": False,
        }])
        refund.action_post()
        self._asaas_pay_received_split(refund, journal, today)
        return refund

    @api.model
    def _asaas_pay_received_split(self, move, journal, payment_date):
        return self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=move.ids,
        ).create({
            "journal_id": journal.id,
            "payment_date": payment_date,
        })._create_payments()

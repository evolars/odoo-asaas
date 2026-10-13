import logging
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import consteq, hmac as hmac_tool

from odoo.addons.asaas_base.models.asaas_client import AsaasError, is_foreign_payer
from odoo.addons.payment_asaas import const

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    # O que o comprador precisa para pagar sem sair do site. Fica na transação para a
    # página de confirmação (e quem voltar a ela) mostrar o mesmo Pix ou boleto.
    asaas_pix_qr_code = fields.Text(string="QR Code Pix (base64)", copy=False, readonly=True)
    asaas_pix_payload = fields.Char(string="Pix copia e cola", copy=False, readonly=True)
    asaas_pix_expiration = fields.Char(string="Pix válido até", copy=False, readonly=True)
    asaas_boleto_url = fields.Char(string="Boleto (PDF)", copy=False, readonly=True)
    asaas_boleto_line = fields.Char(string="Linha digitável", copy=False, readonly=True)
    # Cartão parcelado: o Asaas cria uma cobrança por parcela, todas do mesmo parcelamento.
    asaas_installment_id = fields.Char(string="Parcelamento no Asaas", copy=False, readonly=True, index="btree_not_null")
    asaas_installment_count = fields.Integer(string="Parcelas", copy=False, readonly=True)

    def _get_specific_processing_values(self, processing_values):
        """Token que autoriza o navegador a criar a cobrança desta transação.

        No fluxo direto quem cria a cobrança é a rota `/payment/asaas/charge`,
        chamada pelo navegador; o token prova que a chamada vem de quem abriu a
        transação, sem expor a referência de outra compra.
        """
        res = super()._get_specific_processing_values(processing_values)
        if self.provider_code != "asaas":
            return res
        return {"asaas_access_token": self._asaas_access_token()}

    def _asaas_access_token(self):
        # Não usa payment.utils.generate_access_token: ele exige requisição HTTP, e o
        # token também é gerado no template da confirmação e nos testes.
        self.ensure_one()
        return hmac_tool(self.env(su=True), "payment_asaas", self.reference)

    def _asaas_check_access_token(self, access_token):
        self.ensure_one()
        return bool(access_token) and consteq(access_token, self._asaas_access_token())

    # ------------------------------------------------------------------ #
    # Fluxo direto: Pix, boleto e cartão no próprio site                  #
    # ------------------------------------------------------------------ #

    def _asaas_create_direct_charge(self, card=None, remote_ip=None):
        """Cria a cobrança no tipo do método escolhido e guarda como pagar.

        Pix e boleto deixam a transação pendente com o QR Code ou a linha
        digitável; cartão é cobrado na hora e já volta pago ou recusado. Os dados
        do cartão passam direto para o Asaas: não são gravados nem vão para o log.
        """
        self.ensure_one()
        if self.provider_reference:
            # Duplo clique ou página recarregada: a cobrança já existe.
            return
        billing_type = const.BILLING_TYPE_MAPPING.get(self.payment_method_code)
        if not billing_type:
            raise ValidationError(_("Método de pagamento não aceito pelo Asaas."))

        client = self.provider_id._asaas_get_client()
        partner = self.partner_id
        if (
            not partner.vat
            and not is_foreign_payer(partner)
            and not self.env.context.get("asaas_payer_document")
        ):
            raise ValidationError(_(
                "Informe o CPF ou CNPJ de %s para pagar pelo Asaas.", partner.display_name
            ))
        customer_id = partner._asaas_ensure_customer(client)
        payload = self._asaas_prepare_payment_payload(customer_id)
        payload.pop("callback", None)  # ninguém sai do site, não há para onde voltar
        payload["billingType"] = billing_type
        installments = 1
        if billing_type == "CREDIT_CARD":
            payload.update(self._asaas_prepare_card_payload(card or {}, remote_ip))
            installments = self._asaas_card_installments(card or {})
            if installments > 1:
                # Parcelado: o Asaas divide o total (a primeira parcela pode levar os centavos).
                payload.pop("value", None)
                payload["installmentCount"] = installments
                payload["totalValue"] = float(self.amount)

        try:
            payment = client.create_payment(payload)
        except AsaasError as error:
            if billing_type == "CREDIT_CARD":
                # Cartão recusado: a transação morre aqui e o comprador tenta de
                # novo (outra transação), com a mensagem do Asaas na tela.
                self._set_error(str(error.args[0]) if error.args else _("Cartão recusado."))
            raise

        self.provider_reference = payment["id"]
        if installments > 1:
            self.write({
                "asaas_installment_id": payment.get("installment") or False,
                "asaas_installment_count": installments,
            })
        if billing_type == "PIX":
            qr_code = client.get_pix_qrcode(payment["id"])
            self.write({
                "asaas_pix_qr_code": qr_code.get("encodedImage"),
                "asaas_pix_payload": qr_code.get("payload"),
                "asaas_pix_expiration": qr_code.get("expirationDate"),
            })
        elif billing_type == "BOLETO":
            values = {"asaas_boleto_url": payment.get("bankSlipUrl")}
            try:
                values["asaas_boleto_line"] = client.get_identification_field(
                    payment["id"]
                ).get("identificationField")
            except AsaasError:
                # A linha digitável às vezes demora a ficar pronta; o PDF basta.
                _logger.info("Asaas: linha digitável ainda indisponível em %s.", self.reference)
            self.write(values)
        self._process("asaas", payment)

    def _asaas_set_payer_document(self, document):
        """CPF/CNPJ informado no pagamento por quem ainda não tinha documento.

        A proposta paga pelo portal não passa pelo checkout da loja, onde o CPF é
        pedido: sem isto o Asaas recusaria a cobrança e o cliente não teria onde
        corrigir. O documento fica no contato (a localização valida o número).
        """
        self.ensure_one()
        partner = self.partner_id.sudo()
        if partner.vat or not document:
            return self
        digits = re.sub(r"\D", "", document)
        if len(digits) == 11:
            vat = "%s.%s.%s-%s" % (digits[:3], digits[3:6], digits[6:9], digits[9:])
        elif len(digits) == 14:
            vat = "%s.%s.%s/%s-%s" % (digits[:2], digits[2:5], digits[5:8], digits[8:12], digits[12:])
        else:
            raise ValidationError(_("Informe um CPF (11 dígitos) ou um CNPJ (14 dígitos)."))
        # Conferido aqui, e não só pela localização: sem a brasileira instalada, o `vat`
        # aceitaria qualquer número e o Asaas recusaria a cobrança depois.
        if not self._asaas_document_is_valid(digits):
            raise ValidationError(_("CPF ou CNPJ inválido. Confira os números."))
        try:
            with self.env.cr.savepoint():
                partner.vat = vat
        except ValidationError:
            # Já usado por outro contato (a localização pode não deixar repetir CPF):
            # segue só na cobrança, sem mexer nos cadastros.
            return self.with_context(asaas_payer_document=digits)
        return self

    @staticmethod
    def _asaas_document_is_valid(digits):
        """Dígitos verificadores de CPF (11) ou CNPJ (14)."""
        if len(set(digits)) == 1:
            return False
        if len(digits) == 11:
            for size in (9, 10):
                total = sum(int(digits[i]) * (size + 1 - i) for i in range(size))
                if int(digits[size]) != (total * 10 % 11) % 10:
                    return False
            return True
        if len(digits) == 14:
            for size in (12, 13):
                weights = ([5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2] if size == 12
                           else [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
                rest = sum(int(digits[i]) * weights[i] for i in range(size)) % 11
                if int(digits[size]) != (0 if rest < 2 else 11 - rest):
                    return False
            return True
        return False

    def _asaas_card_installments(self, card):
        """Parcelas pedidas pelo comprador, conferidas com o que o provedor permite."""
        self.ensure_one()
        try:
            count = int(card.get("installments") or 1)
        except (TypeError, ValueError):
            count = 0
        if count not in self.provider_id._asaas_installment_counts(self.amount):
            raise ValidationError(_("Número de parcelas não disponível para este valor."))
        return count

    def _asaas_prepare_card_payload(self, card, remote_ip):
        """Corpo do cartão e do titular para a cobrança `CREDIT_CARD`."""
        self.ensure_one()
        digits = lambda value: re.sub(r"\D", "", value or "")
        number = digits(card.get("number"))
        month, _sep, year = (card.get("expiry") or "").partition("/")
        month, year = digits(month), digits(year)
        if len(year) == 2:
            year = "20" + year
        holder_name = (card.get("holder") or "").strip()
        cvv = digits(card.get("cvv"))
        if not (number and holder_name and len(month) in (1, 2) and len(year) == 4 and cvv):
            raise ValidationError(_("Confira os dados do cartão: número, nome, validade e CVV."))

        partner = self.partner_id
        holder_info = {
            "name": holder_name,
            "email": partner.email or "",
            "postalCode": digits(partner.zip),
            "addressNumber": self._asaas_address_number(partner),
            "addressComplement": partner.street2 or None,
            "phone": digits(partner.phone),
        }
        if is_foreign_payer(partner):
            # O documento de quem mora fora não é CPF: o Asaas recusaria o titular.
            document = digits(card.get("holder_document"))
        else:
            document = digits(card.get("holder_document")) or digits(
                partner.cnpj_cpf_stripped if "cnpj_cpf_stripped" in partner._fields else partner.vat
            ) or digits(partner.vat)
        if document:
            holder_info["cpfCnpj"] = document
        return {
            "creditCard": {
                "holderName": holder_name,
                "number": number,
                "expiryMonth": month.zfill(2),
                "expiryYear": year,
                "ccv": cvv,
            },
            "creditCardHolderInfo": holder_info,
            "remoteIp": remote_ip or None,
        }

    def _asaas_sync_from_api(self):
        """Busca a situação da cobrança no Asaas, sem esperar o webhook."""
        self.ensure_one()
        if not self.provider_reference:
            return
        payment = self.provider_id._asaas_get_client().get_payment(self.provider_reference)
        self._process("asaas", payment)

    @staticmethod
    def _asaas_address_number(partner):
        """Número do endereço, que o Asaas exige do titular do cartão."""
        number = partner.street_number if "street_number" in partner._fields else False
        if not number:
            match = re.search(r"\b(\d+)\b", partner.street or "")
            number = match.group(1) if match else "S/N"
        return number

    def _get_specific_rendering_values(self, processing_values):
        """Cria a cobrança no Asaas e devolve para onde mandar o comprador."""
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != "asaas":
            return res

        client = self.provider_id._asaas_get_client()
        partner = self.partner_id
        if not partner.vat and not is_foreign_payer(partner):
            # O Asaas recusa a cobrança sem CPF/CNPJ. Melhor dizer isso aqui, com
            # o nome do campo, do que deixar a API responder um erro genérico.
            raise ValidationError(_(
                "Informe o CPF ou CNPJ de %s para pagar pelo Asaas.", partner.display_name
            ))

        customer_id = partner._asaas_ensure_customer(client)
        payload = self._asaas_prepare_payment_payload(customer_id)
        try:
            payment = client.create_payment(payload)
        except AsaasError as error:
            if not self._asaas_is_callback_domain_error(error):
                raise
            # A volta automática para a loja exige o site cadastrado na conta do
            # Asaas (Minha Conta → Informações). Sem ele, a cobrança inteira era
            # recusada e ninguém pagava. A confirmação vem pelo webhook; perder a
            # volta automática é bem menos grave que perder a venda.
            _logger.warning(
                "Asaas: conta sem domínio cadastrado; cobrança %s criada sem volta "
                "automática para a loja.", self.reference,
            )
            payload.pop("callback", None)
            payment = client.create_payment(payload)

        self.provider_reference = payment["id"]
        invoice_url = payment.get("invoiceUrl")
        if not invoice_url:
            raise ValidationError(_("O Asaas não devolveu a página de pagamento da cobrança."))
        return {"api_url": invoice_url, "url_params": {}}

    @staticmethod
    def _asaas_is_callback_domain_error(error):
        """Recusa por falta de site cadastrado, que vale só para o `callback`."""
        errors = (getattr(error, "payload", None) or {}).get("errors") or []
        text = " ".join(
            "%s %s" % (item.get("code", ""), item.get("description", "")) for item in errors
        ).lower()
        return "domínio" in text or "dominio" in text or "domain" in text

    def _asaas_prepare_payment_payload(self, customer_id):
        self.ensure_one()
        base_url = self.provider_id.get_base_url().rstrip("/")
        due_date = fields.Date.context_today(self) + timedelta(days=const.DEFAULT_DUE_DAYS)
        return {
            "customer": customer_id,
            # UNDEFINED deixa o comprador escolher Pix, boleto ou cartão na página do Asaas
            "billingType": "UNDEFINED",
            "value": float(self.amount),
            "dueDate": due_date.isoformat(),
            "description": self.reference,
            "externalReference": self.reference,
            "callback": {
                "successUrl": "%s/payment/status" % base_url,
                "autoRedirect": True,
            },
        }

    @api.model
    def _extract_reference(self, provider_code, payment_data):
        if provider_code != "asaas":
            return super()._extract_reference(provider_code, payment_data)
        return payment_data.get("externalReference")

    @api.model
    def _search_by_reference(self, provider_code, payment_data):
        """Acha a transação pela referência externa, pelo id da cobrança ou pelo parcelamento.

        A cobrança criada fora do checkout (faturamento, assinatura) não tem transação:
        devolve vazio, como o `payment` do 19, em vez de exceção.
        """
        if provider_code != "asaas":
            return super()._search_by_reference(provider_code, payment_data)

        tx = self
        reference = payment_data.get("externalReference")
        if reference:
            tx = self.search([("reference", "=", reference), ("provider_code", "=", "asaas")])
        if not tx and payment_data.get("id"):
            tx = self.search([
                ("provider_reference", "=", payment_data["id"]),
                ("provider_code", "=", "asaas"),
            ])
        if not tx and payment_data.get("installment"):
            # Outra parcela do mesmo cartão: a transação é a do parcelamento.
            tx = self.search([
                ("asaas_installment_id", "=", payment_data["installment"]),
                ("provider_code", "=", "asaas"),
            ], limit=1)
        if not tx:
            _logger.info(
                "Asaas: nenhuma transação para a cobrança %s.",
                payment_data.get("id") or reference or "?",
            )
        return tx

    def _extract_amount_data(self, payment_data):
        """Valor da cobrança, para o `payment` conferir com o da transação.

        Cartão parcelado não é conferido: cada parcela chega com o valor dela, não com o
        total. Dado sem `value` (consulta parcial) também não.
        """
        if self.provider_code != "asaas":
            return super()._extract_amount_data(payment_data)
        if self.asaas_installment_id or payment_data.get("installment") or payment_data.get("value") is None:
            return None
        return {"amount": float(payment_data["value"]), "currency_code": "BRL", "precision_digits": 2}

    def _apply_updates(self, payment_data):
        if self.provider_code != "asaas":
            return super()._apply_updates(payment_data)

        if not self.provider_reference and payment_data.get("id"):
            self.provider_reference = payment_data["id"]

        status = (payment_data.get("status") or "").upper()
        if not status:
            self._set_error(_("Asaas: a cobrança chegou sem situação."))
            return

        if status in const.PAYMENT_STATUS_MAPPING["pending"]:
            self._set_pending()
        elif status in const.PAYMENT_STATUS_MAPPING["done"]:
            self._set_done()
        elif status in const.PAYMENT_STATUS_MAPPING["cancel"]:
            self._set_canceled(state_message=_("Asaas informou a situação %s.", status))
        elif status in const.PAYMENT_STATUS_MAPPING["error"]:
            self._set_error(_("Asaas informou a situação %s.", status))
        else:
            _logger.info("Asaas: situação não mapeada '%s' na transação %s.", status, self.reference)
            self._set_error(_("Asaas devolveu uma situação desconhecida: %s.", status))

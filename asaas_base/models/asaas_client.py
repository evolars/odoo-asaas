"""Cliente HTTP da API v3 do Asaas.

Deliberadamente sem acoplamento com o Odoo: recebe credencial explícita e devolve
dicionários. Quem quiser a credencial vinda das configurações usa
`env['asaas.config'].get_client()`; quem tem credencial própria — como um
`payment.provider` — instancia direto.
"""
import logging

import requests

from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

SANDBOX_URL = "https://api-sandbox.asaas.com/v3"
PRODUCTION_URL = "https://api.asaas.com/v3"
DEFAULT_TIMEOUT = 30

# O Asaas exige User-Agent em contas root criadas a partir de 13/06/2024; sem ele
# a requisição é recusada. Ver https://docs.asaas.com/docs/autenticacao-1
USER_AGENT = "Evolars-Odoo/17.0"


class AsaasError(UserError):
    """O Asaas recusou a operação, ou não deu para falar com ele.

    Herda de UserError para continuar aparecendo como mensagem tratável na
    interface, mas dá o que capturar quando o chamador precisa decidir o que
    fazer — um checkout não pode abrir diálogo de erro no rosto do comprador.
    """

    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


class AsaasClient:
    def __init__(self, api_key, sandbox=True, timeout=DEFAULT_TIMEOUT, user_agent=USER_AGENT):
        self.api_key = api_key
        self.sandbox = sandbox
        self.timeout = timeout
        self.user_agent = user_agent
        self.base_url = SANDBOX_URL if sandbox else PRODUCTION_URL

    # ------------------------------------------------------------------ #
    # Transporte                                                          #
    # ------------------------------------------------------------------ #

    def request(self, method, path, payload=None, params=None):
        if not self.api_key:
            raise AsaasError(_("Configure a chave de API do Asaas antes de sincronizar."))
        url = "%s/%s" % (self.base_url, path.lstrip("/"))
        headers = {
            "access_token": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }
        try:
            response = requests.request(
                method, url, headers=headers, json=payload, params=params, timeout=self.timeout,
            )
        except requests.RequestException as error:
            _logger.warning("Asaas: falha de conexão em %s %s: %s", method, path, error)
            raise AsaasError(_("Não foi possível conectar ao Asaas.")) from error

        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {}

        if not response.ok:
            message = "; ".join(
                item.get("description", "")
                for item in (data.get("errors") or [])
                if item.get("description")
            )
            # a chave nunca entra no log
            _logger.warning(
                "Asaas: recusa status=%s em %s %s", response.status_code, method, path,
            )
            raise AsaasError(
                _("O Asaas recusou a operação: %s") % (message or response.reason),
                status_code=response.status_code,
                payload=data,
            )
        return data

    def ping(self):
        """Valida a credencial com a chamada mais barata que existe."""
        return self.request("GET", "/finance/balance")

    # ------------------------------------------------------------------ #
    # Clientes                                                            #
    # ------------------------------------------------------------------ #

    def create_customer(self, payload):
        return self.request("POST", "/customers", payload)

    def get_customer(self, customer_id):
        return self.request("GET", "/customers/%s" % customer_id)

    def update_customer(self, customer_id, payload):
        return self.request("PUT", "/customers/%s" % customer_id, payload)

    # ------------------------------------------------------------------ #
    # Cobranças                                                           #
    # ------------------------------------------------------------------ #

    def create_payment(self, payload):
        return self.request("POST", "/payments", payload)

    def get_payment(self, payment_id):
        return self.request("GET", "/payments/%s" % payment_id)

    def update_payment(self, payment_id, payload):
        return self.request("PUT", "/payments/%s" % payment_id, payload)

    def delete_payment(self, payment_id):
        return self.request("DELETE", "/payments/%s" % payment_id)

    def refund_payment(self, payment_id, value=None, description=None):
        payload = {}
        if value is not None:
            payload["value"] = value
        if description:
            payload["description"] = description
        return self.request("POST", "/payments/%s/refund" % payment_id, payload)

    def receive_in_cash(self, payment_id, payment_date, value=None, notify_customer=False):
        payload = {"paymentDate": payment_date, "notifyCustomer": notify_customer}
        if value is not None:
            payload["value"] = value
        return self.request("POST", "/payments/%s/receiveInCash" % payment_id, payload)

    def get_pix_qrcode(self, payment_id):
        return self.request("GET", "/payments/%s/pixQrCode" % payment_id)

    def get_identification_field(self, payment_id):
        return self.request("GET", "/payments/%s/identificationField" % payment_id)

    def resend_payment_notification(self, payment_id):
        return self.request("POST", "/payments/%s/resendPaymentNotification" % payment_id)

    # ------------------------------------------------------------------ #
    # Assinaturas                                                         #
    # ------------------------------------------------------------------ #

    def create_subscription(self, payload):
        return self.request("POST", "/subscriptions", payload)

    def get_subscription(self, subscription_id):
        return self.request("GET", "/subscriptions/%s" % subscription_id)

    def update_subscription(self, subscription_id, payload):
        return self.request("PUT", "/subscriptions/%s" % subscription_id, payload)

    def delete_subscription(self, subscription_id):
        return self.request("DELETE", "/subscriptions/%s" % subscription_id)

    def get_subscription_payments(self, subscription_id):
        return self.request("GET", "/subscriptions/%s/payments" % subscription_id)

    # ------------------------------------------------------------------ #
    # Financeiro e transferências                                         #
    # ------------------------------------------------------------------ #

    def get_balance(self):
        return self.request("GET", "/finance/balance")

    def transfer_pix(self, value, pix_address_key, pix_address_key_type,
                     description=None, external_reference=None, schedule_date=None):
        payload = {
            "value": float(value),
            "operationType": "PIX",
            "pixAddressKey": pix_address_key,
            "pixAddressKeyType": pix_address_key_type,
        }
        if description:
            payload["description"] = description[:100]
        if external_reference:
            payload["externalReference"] = external_reference
        if schedule_date:
            payload["scheduleDate"] = schedule_date
        return self.request("POST", "/transfers", payload)

    def get_transfer(self, transfer_id):
        return self.request("GET", "/transfers/%s" % transfer_id)

    # ------------------------------------------------------------------ #
    # Webhooks                                                            #
    # ------------------------------------------------------------------ #

    PAYMENT_EVENTS = [
        "PAYMENT_CREATED", "PAYMENT_AWAITING_RISK_ANALYSIS", "PAYMENT_APPROVED_BY_RISK_ANALYSIS",
        "PAYMENT_REPROVED_BY_RISK_ANALYSIS", "PAYMENT_AUTHORIZED", "PAYMENT_UPDATED",
        "PAYMENT_CONFIRMED", "PAYMENT_RECEIVED", "PAYMENT_CREDIT_CARD_CAPTURE_REFUSED",
        "PAYMENT_ANTICIPATED", "PAYMENT_OVERDUE", "PAYMENT_DELETED", "PAYMENT_RESTORED",
        "PAYMENT_REFUNDED", "PAYMENT_PARTIALLY_REFUNDED", "PAYMENT_REFUND_IN_PROGRESS",
        "PAYMENT_REFUND_DENIED", "PAYMENT_CHARGEBACK_REQUESTED", "PAYMENT_CHARGEBACK_DISPUTE",
        "PAYMENT_AWAITING_CHARGEBACK_REVERSAL", "PAYMENT_DUNNING_RECEIVED",
        "PAYMENT_DUNNING_REQUESTED", "PAYMENT_BANK_SLIP_VIEWED", "PAYMENT_CHECKOUT_VIEWED",
        "PAYMENT_SPLIT_DONE", "PAYMENT_SPLIT_CANCELLED", "PAYMENT_SPLIT_DIVERGENCE_BLOCK",
        "PAYMENT_RECEIVED_IN_CASH_UNDONE", "PAYMENT_BANK_SLIP_CANCELLED",
    ]
    SUBSCRIPTION_EVENTS = [
        "SUBSCRIPTION_CREATED", "SUBSCRIPTION_UPDATED", "SUBSCRIPTION_DELETED",
        "SUBSCRIPTION_INACTIVATED", "SUBSCRIPTION_SPLIT_DISABLED",
        "SUBSCRIPTION_SPLIT_DIVERGENCE_BLOCK_FINISHED",
    ]
    TRANSFER_EVENTS = [
        "TRANSFER_CREATED", "TRANSFER_PENDING", "TRANSFER_IN_BANK_PROCESSING",
        "TRANSFER_BLOCKED", "TRANSFER_DONE", "TRANSFER_FAILED", "TRANSFER_CANCELLED",
    ]
    BILL_EVENTS = ["BILL_CREATED", "BILL_PAID", "BILL_CANCELLED", "BILL_FAILED", "BILL_REFUNDED"]
    ALL_WEBHOOK_EVENTS = PAYMENT_EVENTS + SUBSCRIPTION_EVENTS + TRANSFER_EVENTS + BILL_EVENTS

    def get_webhooks(self):
        return self.request("GET", "/webhooks")

    def create_webhook(self, payload):
        return self.request("POST", "/webhooks", payload)

    def update_webhook(self, webhook_id, payload):
        return self.request("PUT", "/webhooks/%s" % webhook_id, payload)

    def sync_webhook(self, name, target_url, auth_token, email, events=None):
        """Cria ou atualiza o webhook, casando por URL ou por nome."""
        events = self.ALL_WEBHOOK_EVENTS if events is None else events
        payload = {
            "name": name,
            "url": target_url,
            "email": email,
            "enabled": True,
            "interrupted": False,
            "apiVersion": 3,
            "authToken": auth_token,
            "sendType": "NON_SEQUENTIALLY",
            "events": events,
        }
        for item in (self.get_webhooks().get("data") or []):
            if item.get("url") == target_url or item.get("name") == name:
                return self.update_webhook(item["id"], payload)
        return self.create_webhook(payload)


def customer_payload(partner, notification_disabled=True):
    """Traduz um `res.partner` para o corpo de /customers.

    Fica aqui, e não num modelo, porque tanto o módulo de faturamento quanto o
    provedor de pagamento precisam da mesma tradução.
    """
    payload = {
        "name": partner.name,
        "externalReference": "odoo-partner-%s" % partner.id,
        "notificationDisabled": notification_disabled,
    }
    optional = {
        "email": partner.email,
        "phone": partner.phone,
        "mobilePhone": partner.mobile,
        "address": partner.street,
        "complement": partner.street2,
        "city": partner.city,
    }
    payload.update({key: value for key, value in optional.items() if value})
    if partner.zip:
        payload["postalCode"] = partner.zip.replace("-", "").replace(".", "")
    if partner.state_id:
        payload["state"] = partner.state_id.code
    # A localização brasileira da OCA mantém o documento já normalizado em
    # `cnpj_cpf_stripped`; fora dela, sobra limpar o `vat` na mão.
    documento = partner.vat
    if "cnpj_cpf_stripped" in partner._fields:
        documento = partner.cnpj_cpf_stripped or partner.vat
    if documento:
        payload["cpfCnpj"] = "".join(char for char in documento if char.isalnum())
    return payload

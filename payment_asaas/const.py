# O Asaas opera em reais. Cobrar em outra moeda não é questão de configuração:
# a conta simplesmente não recebe.
SUPPORTED_CURRENCIES = ["BRL"]

# Métodos que o comprador escolhe na página do Asaas. `UNDEFINED` no billingType
# deixa os três disponíveis de uma vez, que é o comportamento desejado no varejo.
DEFAULT_PAYMENT_METHOD_CODES = {"pix", "boleto", "card"}

# Estados da cobrança -> o que a transação do Odoo vira.
# https://docs.asaas.com/docs/webhook-para-cobrancas
PAYMENT_STATUS_MAPPING = {
    "pending": ("PENDING", "AWAITING_RISK_ANALYSIS"),
    "done": ("RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH"),
    "cancel": ("OVERDUE", "DELETED", "REFUNDED", "REFUND_REQUESTED", "CHARGEBACK_REQUESTED"),
    "error": ("AWAITING_CHARGEBACK_REVERSAL", "CHARGEBACK_DISPUTE"),
}

# Quantos dias o boleto/Pix fica de pé antes de vencer.
DEFAULT_DUE_DAYS = 3

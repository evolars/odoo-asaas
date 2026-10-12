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

# Situação da cobrança com o nome que o painel do Asaas mostra (e exporta).
# https://docs.asaas.com/reference/recuperar-uma-unica-cobranca
CHARGE_STATUS_SELECTION = [
    ("PENDING", "Aguardando pagamento"),
    ("AWAITING_RISK_ANALYSIS", "Pagamento em análise"),
    ("CONFIRMED", "Confirmada"),
    ("RECEIVED", "Recebida"),
    ("RECEIVED_IN_CASH", "Recebida em dinheiro"),
    ("OVERDUE", "Vencida"),
    ("REFUND_REQUESTED", "Estorno solicitado"),
    ("REFUND_IN_PROGRESS", "Estorno em processamento"),
    ("REFUNDED", "Estornada"),
    ("CHARGEBACK_REQUESTED", "Chargeback recebido"),
    ("CHARGEBACK_DISPUTE", "Em disputa de chargeback"),
    ("AWAITING_CHARGEBACK_REVERSAL", "Disputa vencida, aguardando repasse da adquirente"),
    ("DUNNING_REQUESTED", "Em processo de negativação"),
    ("DUNNING_RECEIVED", "Recuperada"),
    ("DELETED", "Removida"),
]

# Forma de pagamento da cobrança, como no painel do Asaas.
BILLING_TYPE_SELECTION = [
    ("PIX", "Pix"),
    ("BOLETO", "Boleto"),
    ("CREDIT_CARD", "Cartão de crédito"),
    ("DEBIT_CARD", "Cartão de débito"),
    ("TRANSFER", "Transferência"),
    ("DEPOSIT", "Depósito"),
    ("UNDEFINED", "Pergunte ao cliente"),
]

# Quantos dias o boleto/Pix fica de pé antes de vencer.
DEFAULT_DUE_DAYS = 3

# Método do checkout -> `billingType` da cobrança. Cada método gera a cobrança
# já no tipo certo, para mostrar o Pix, o boleto ou cobrar o cartão no próprio site.
BILLING_TYPE_MAPPING = {
    "pix": "PIX",
    "boleto": "BOLETO",
    "card": "CREDIT_CARD",
}

# Texto da transação pendente (Pix ou boleto à espera do pagamento).
PENDING_MSG = (
    "<p>Pedido recebido. Assim que o pagamento for confirmado, o pedido segue para "
    "separação e você recebe um e-mail.</p>"
)

# O Asaas recusa parcela de cartão abaixo de R$ 5.
MIN_INSTALLMENT_VALUE = 5.0

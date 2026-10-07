{
    "name": "Asaas - Provedor de Pagamento",
    "version": "18.0.1.4.0",
    "category": "Accounting/Payment Providers",
    "summary": "Receber Pix, boleto e cartão pelo Asaas no checkout do site",
    "description": """
Provedor de pagamento Asaas para o checkout do Odoo (`website_sale` e portal).

Checkout transparente: o comprador paga sem sair do site. O Odoo garante o
cliente no Asaas e cria a cobrança já no tipo do método escolhido. Cartão é
cobrado na hora (os dados vão direto para o Asaas, sem ficar gravados); Pix e
boleto deixam o pedido pendente e a confirmação do pedido mostra o QR Code, o
"copia e cola" ou a linha digitável, atualizando sozinha quando o pagamento cai.
O que confirma Pix e boleto é o **webhook** (a página também consulta o Asaas).

O fluxo de redirecionamento (`billingType: UNDEFINED`, página do Asaas) continua
disponível para quem chamar a transação nesse fluxo.

Pré-requisitos para funcionar de ponta a ponta:

* Configurações → Asaas com o token do webhook definido e o webhook registrado
  (o `asaas_base` faz isso em um clique).
* CPF/CNPJ preenchido no cliente — o Asaas recusa cobrança sem documento.
* Moeda BRL: o Asaas não liquida em outra.

O ambiente segue o estado do provedor. Em *Teste* a integração fala com o
Sandbox, onde as cobranças são aprovadas automaticamente e nada é movimentado.
    """,
    "author": "Evolars LTDA",
    "website": "https://evolars.com.br",
    "license": "OPL-1",
    "depends": ["payment", "asaas_base"],
    "data": [
        "views/payment_asaas_templates.xml",
        "views/payment_provider_views.xml",
        "data/payment_provider_data.xml",  # depende das views dos formulários
        "data/payment_asaas_setup.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "payment_asaas/static/src/js/payment_form.js",
            "payment_asaas/static/src/js/post_processing.js",
            "payment_asaas/static/src/js/transaction_instructions.js",
        ],
    },
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
}

{
    "name": "Asaas - Provedor de Pagamento",
    "version": "18.0.1.0.0",
    "category": "Accounting/Payment Providers",
    "summary": "Receber Pix, boleto e cartão pelo Asaas no checkout do site",
    "description": """
Provedor de pagamento Asaas para o checkout do Odoo (`website_sale` e portal).

Fluxo de redirecionamento: o Odoo garante o cliente no Asaas, cria a cobrança
com `billingType: UNDEFINED` — deixando Pix, boleto e cartão à escolha do
comprador na página do Asaas — e o manda para lá. O que confirma o pagamento é
o **webhook**, não a volta do navegador: no Pix e no boleto o dinheiro cai
depois que o comprador já fechou a aba.

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
        "data/payment_provider_data.xml",  # depende da view do formulário de redirecionamento
    ],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
}

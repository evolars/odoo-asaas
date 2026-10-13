{
    "name": "Asaas - Pix e boleto na confirmação da loja",
    "version": "19.0.1.0.0",
    "category": "Website/Website",
    "summary": "Mostra o QR Code do Pix e o boleto do Asaas na confirmação do pedido da loja",
    "description": """
Ponte entre o `website_sale` e o `payment_asaas`, instalada sozinha quando os dois
estão na base.

A página de confirmação da loja (`/shop/confirmation`) não usa o cabeçalho de
status do `payment`, então o Pix e o boleto do checkout transparente não
apareceriam nela. Aqui eles entram no bloco de status do pagamento, para qualquer
produto: livro, curso do eLearning (`website_sale_slides`), serviço. A página se
atualiza sozinha quando o pagamento cai.
    """,
    "author": "Evolars LTDA",
    "website": "https://evolars.com.br",
    "license": "OPL-1",
    "depends": ["website_sale", "payment_asaas"],
    "data": [
        "views/website_sale_templates.xml",
    ],
    "auto_install": True,
    "installable": True,
}

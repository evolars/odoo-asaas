{
    "name": "Asaas - Base",
    "version": "17.0.1.0.0",
    "category": "Accounting/Payment",
    "summary": "Cliente da API Asaas v3, credenciais e recepção autenticada de webhooks",
    "description": """
Camada compartilhada da integração com o Asaas.

Entrega três coisas e nada além disso:

* **Cliente de API** (`AsaasClient`) — sem acoplamento com o Odoo: recebe a
  credencial explicitamente, o que permite tanto usar a chave global das
  Configurações quanto a chave própria de um `payment.provider`. Envia o header
  `User-Agent`, que o Asaas exige em contas root criadas a partir de 13/06/2024.
* **Credenciais e ambiente** em Configurações → Asaas, com teste de conexão e
  registro do webhook em um clique.
* **Recepção de webhook** autenticada por `asaas-access-token` com comparação em
  tempo constante. Todo evento é gravado antes de ser processado, e entregue por
  `asaas.webhook.event._dispatch()` para quem souber tratá-lo.

Sozinho não cobra nem concilia nada — quem faz isso são os módulos que dependem
dele (`evolars_asaas`, `payment_asaas`).
    """,
    "author": "Evolars LTDA",
    "website": "https://evolars.com.br",
    "license": "OPL-1",
    "depends": ["base_setup"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/ir.model.access.csv",
        "views/res_config_settings_views.xml",
    ],
    "images": ["static/description/icon.png"],
    "installable": True,
    "application": False,
}

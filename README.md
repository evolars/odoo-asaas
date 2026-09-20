# Odoo Asaas

Integração do [Asaas](https://www.asaas.com) com Odoo 17, em módulos separados para poderem ser
combinados conforme o cliente precisa.

Propriedade da **Evolars LTDA**.

---

## Módulos

| Módulo | O que faz | Depende de | Licença |
|---|---|---|---|
| `asaas_base` | Cliente da API v3, credenciais, recepção autenticada de webhooks | `base_setup` | OPL-1 |
| `payment_asaas` | Provedor de pagamento para o checkout do site | `payment`, `asaas_base` | OPL-1 |
| `evolars_asaas` | Cobranças, assinaturas, splits, reembolsos e transferências | `account`, `contract`, `asaas_base` | AGPL-3 |

Instale só o que for usar. Uma loja que apenas recebe pelo site quer `payment_asaas`; uma
operação de recorrência quer `evolars_asaas`; quem faz as duas coisas instala os dois, e eles
compartilham a mesma credencial e o mesmo webhook.

---

## `asaas_base`

### Cliente de API

`AsaasClient` não conhece o Odoo: recebe a credencial explicitamente e devolve dicionários.
Isso é o que permite a chave global das Configurações e a chave própria de um `payment.provider`
conviverem sem duas implementações.

```python
from odoo.addons.asaas_base.models.asaas_client import AsaasClient

client = AsaasClient(api_key, sandbox=True)
client.create_payment({...})
```

Quem quiser a credencial das Configurações usa a fábrica:

```python
client = env["asaas.config"].get_client()
```

O header `User-Agent` vai em toda requisição — o Asaas **exige** em contas root criadas a partir
de 13/06/2024, e sem ele a chamada é recusada.

Erros viram `AsaasError`, que herda de `UserError` (continua sendo mensagem tratável na
interface) mas carrega `status_code` e `payload` para quem precisa decidir o que fazer.

### Configuração

Configurações → Asaas: ambiente, chave de API e token do webhook, com **Testar conexão** e
**Registrar webhook no Asaas** em um clique.

As chaves ficam em `asaas.api_key`, `asaas.environment` e `asaas.webhook_token`. Bases que
vinham do `evolars_asaas` continuam funcionando: se a chave nova estiver vazia, a antiga
(`evolars_asaas.*`) é lida como fallback.

### Webhook

Endpoint em `/asaas/webhook`. O antigo `/evolars_asaas/webhook` continua atendendo, porque
bases existentes têm esse endereço registrado do lado do Asaas.

Autenticação por `asaas-access-token` com `hmac.compare_digest`. **Sem token configurado no
Odoo, todo evento é recusado com 403** — é proposital: um webhook sem segredo aceita qualquer
um que descubra a URL.

Todo evento é gravado em `asaas.webhook.event` **antes** de ser processado, e só então
entregue a `_dispatch()`. Evento recebido e não tratado é um bug investigável; evento perdido
não é. Falha no tratamento é registrada no próprio evento e devolve 200 — devolver erro faria
o Asaas reenviar em laço algo que não vai mudar de resultado.

Para tratar eventos no seu módulo:

```python
class AsaasWebhookEvent(models.Model):
    _inherit = "asaas.webhook.event"

    def _dispatch(self, payload):
        handled = super()._dispatch(payload)
        ...
        return True  # se você tratou
```

---

## `payment_asaas`

Provedor de redirecionamento para `website_sale` e portal.

1. Garante o cliente no Asaas a partir do `res.partner` (usa o `asaas_customer_id` do base, sem
   gastar chamada quando já existe)
2. Cria a cobrança com `billingType: UNDEFINED` — Pix, boleto e cartão ficam à escolha do
   comprador na página do Asaas
3. Redireciona para o `invoiceUrl`
4. O **webhook** confirma o pagamento

O passo 4 é o que vale. A volta do navegador não decide nada: no Pix e no boleto o dinheiro cai
depois que o comprador já fechou a aba, e ele pode nunca ser redirecionado. Sem o webhook
registrado, os pedidos ficam pendentes para sempre.

O ambiente segue o **estado do provedor**, não as Configurações: provedor em *Teste* bate no
Sandbox, sempre. Não existe combinação de configuração que faça um provedor de teste emitir
cobrança de verdade.

### Requisitos

* Moeda **BRL** — o Asaas não liquida em outra, e o provedor some do checkout fora dela
* **CPF/CNPJ** no cliente — o Asaas recusa cobrança sem documento. O checkout avisa com o nome
  do campo em vez de deixar a API responder um erro genérico
* Webhook registrado

---

## `evolars_asaas`

Cobranças avulsas, assinaturas recorrentes, splits, reembolsos, recebimento em dinheiro,
transferências Pix e notificações ao cliente (e-mail via Resend e WhatsApp). Concilia os
recebimentos contra o diário configurado.

Veio da base da Evolars e passou a usar o `asaas_base`: o cliente de API próprio, de 237 linhas,
virou um adaptador de 73 que só preserva a assinatura antiga (`AsaasAPI(env)`) para o resto do
módulo não precisar mudar. Código novo deve usar `env["asaas.config"].get_client()`.

O endpoint do webhook também passou a ser o do `asaas_base` — um só, compartilhado com o
provedor de pagamento. O processamento continua em `evolars.asaas.webhook.event`, com a
deduplicação por `external_event_id` e a conciliação de sempre; o que mudou é que a porta de
entrada e a autenticação são únicas.

Depende do `contract` da OCA para as assinaturas: `evolars.asaas.subscription.contract_id` é
obrigatório.

---

## Sandbox

Chave `$aact_hmlg_…` em `https://api-sandbox.asaas.com/v3`. As cobranças são aprovadas
automaticamente, então dá para percorrer o fluxo financeiro inteiro sem mover dinheiro.
Produção usa `$aact_prod_…` em `https://api.asaas.com/v3`.

---

## Instalação via Doodba

Em `custom/src/repos.yaml` do cliente:

```yaml
./odoo-asaas:
  defaults:
    depth: $DEPTH_DEFAULT
  remotes:
    evolars: https://github.com/evolars/odoo-asaas.git
  target: evolars $ODOO_VERSION
  merges:
    - evolars $ODOO_VERSION
```

E em `custom/src/addons.yaml`:

```yaml
odoo-asaas:
  - asaas_base
  - payment_asaas
  - evolars_asaas   # só se o cliente usar cobrança recorrente/faturamento
```

O repositório é privado; o `Dockerfile` do Doodba já recebe `GH_TOKEN` na etapa de agregação.

---

## Testes

```bash
docker run --rm --network <rede> -v "$PWD":/mnt/extra-addons:ro \
  -e HOST=<db> -e USER=odoo -e PASSWORD=odoo odoo:17.0 \
  odoo -d asaas_test --addons-path=/mnt/extra-addons,/usr/lib/python3/dist-packages/odoo/addons \
  -i asaas_base,payment_asaas --test-enable --test-tags /asaas_base,/payment_asaas \
  --stop-after-init --without-demo=all
```

Para incluir o `evolars_asaas` é preciso ter o `contract` da OCA no `--addons-path`.
São **43 testes** no total.

Nenhum teste toca a rede: as chamadas são interceptadas. Cobrem o envio das credenciais e do
`User-Agent`, tradução de erro da API, o corpo enviado ao criar a cobrança, o mapeamento de
situação da cobrança para o estado da transação, e o webhook — inclusive o caso de cobrança
criada fora da loja, que deve ser ignorada em silêncio e não virar erro.

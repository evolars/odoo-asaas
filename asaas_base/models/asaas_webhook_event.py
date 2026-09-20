import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class AsaasWebhookEvent(models.Model):
    _name = "asaas.webhook.event"
    _description = "Evento recebido do Asaas"
    _order = "create_date desc, id desc"

    name = fields.Char(string="Evento", required=True, index=True, readonly=True)
    asaas_id = fields.Char(string="ID no Asaas", index=True, readonly=True)
    payload = fields.Text(readonly=True)
    state = fields.Selection(
        [("pending", "Pendente"), ("done", "Processado"), ("ignored", "Ignorado"), ("error", "Erro")],
        default="pending", required=True, index=True, readonly=True,
    )
    error = fields.Text(readonly=True)

    @api.model
    def ingest(self, payload):
        """Grava o evento e entrega para quem souber tratá-lo.

        A gravação vem antes do processamento de propósito: evento recebido e
        não tratado é um bug que dá para investigar; evento perdido, não.
        """
        event_name = payload.get("event") or "UNKNOWN"
        record = self.sudo().create({
            "name": event_name,
            "asaas_id": (payload.get("payment") or payload.get("subscription")
                         or payload.get("transfer") or {}).get("id"),
            "payload": json.dumps(payload, ensure_ascii=False, indent=2),
        })
        try:
            handled = record._dispatch(payload)
        except Exception as error:  # noqa: BLE001 - o webhook não pode derrubar a resposta
            _logger.exception("Asaas: falha ao processar %s", event_name)
            record.write({"state": "error", "error": str(error)})
            return record
        record.write({"state": "done" if handled else "ignored"})
        return record

    def _dispatch(self, payload):
        """Ponto de extensão: cada módulo trata o que lhe diz respeito.

        Devolve True se alguém tratou. O `asaas_base` sozinho não trata nada —
        ele só garante que o evento chegou, foi autenticado e ficou registrado.
        """
        return False

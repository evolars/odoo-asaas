/** @odoo-module **/

import publicWidget from '@web/legacy/js/public/public_widget';
import { _t } from '@web/core/l10n/translation';
import { rpc } from '@web/core/network/rpc';

/**
 * Pix ou boleto pendente: copiar o código e atualizar a página quando o
 * pagamento cair. A cada quatro consultas, pergunta ao próprio Asaas, caso o
 * webhook demore.
 */
publicWidget.registry.AsaasTransactionInstructions = publicWidget.Widget.extend({
    selector: '.o_asaas_instructions',
    events: {
        'click .o_asaas_copy': '_onCopy',
    },

    start() {
        this.pollCount = 0;
        this._schedulePoll();
        return this._super(...arguments);
    },

    destroy() {
        clearTimeout(this.pollTimer);
        this._super(...arguments);
    },

    _schedulePoll() {
        const delay = this.pollCount < 60 ? 5000 : 30000;
        this.pollTimer = setTimeout(() => this._poll(), delay);
    },

    async _poll() {
        this.pollCount++;
        try {
            const { state } = await rpc('/payment/asaas/poll', {
                reference: this.el.dataset.reference,
                access_token: this.el.dataset.accessToken,
                sync: this.pollCount % 4 === 0,
            });
            if (!['draft', 'pending'].includes(state)) {
                window.location.reload();
                return;
            }
        } catch {
            // Rede caiu ou servidor reiniciando: tenta de novo no próximo ciclo.
        }
        this._schedulePoll();
    },

    async _onCopy(ev) {
        const button = ev.currentTarget;
        const text = button.dataset.copy;
        try {
            await navigator.clipboard.writeText(text);
        } catch {
            const input = button.parentElement.querySelector('input');
            input?.select();
            document.execCommand('copy');
        }
        const label = button.textContent;
        button.textContent = _t("Copiado!");
        setTimeout(() => (button.textContent = label), 2000);
    },
});

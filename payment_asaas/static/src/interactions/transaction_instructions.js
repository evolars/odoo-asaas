import { _t } from '@web/core/l10n/translation';
import { rpc } from '@web/core/network/rpc';
import { registry } from '@web/core/registry';
import { Interaction } from '@web/public/interaction';

/**
 * Pix ou boleto pendente: copiar o código e atualizar a página quando o
 * pagamento cair. A cada quatro consultas, pergunta ao próprio Asaas, caso o
 * webhook demore.
 */
export class AsaasTransactionInstructions extends Interaction {
    static selector = '.o_asaas_instructions';
    dynamicContent = {
        '.o_asaas_copy': { 't-on-click': this.onCopy },
    };

    setup() {
        this.pollCount = 0;
        // A mesma transação pode aparecer duas vezes na página (o pedido no portal chama o
        // cabeçalho de status em dois lugares): só a primeira fica visível e consulta.
        const reference = this.el.dataset.reference;
        const first = [...document.querySelectorAll('.o_asaas_instructions')].find(
            (el) => el.dataset.reference === reference
        );
        this.isDuplicate = first !== this.el;
        if (this.isDuplicate) {
            this.el.classList.add('d-none');
        }
    }

    start() {
        if (!this.isDuplicate) {
            this.schedulePoll();
        }
    }

    schedulePoll() {
        const delay = this.pollCount < 60 ? 5000 : 30000;
        this.waitForTimeout(() => this.poll(), delay);
    }

    async poll() {
        this.pollCount++;
        try {
            const { state } = await this.waitFor(rpc('/payment/asaas/poll', {
                reference: this.el.dataset.reference,
                access_token: this.el.dataset.accessToken,
                sync: this.pollCount % 4 === 0,
            }));
            if (!['draft', 'pending'].includes(state)) {
                window.location.reload();
                return;
            }
        } catch {
            // Rede caiu ou servidor reiniciando: tenta de novo no próximo ciclo.
        }
        this.schedulePoll();
    }

    async onCopy(ev) {
        const button = ev.currentTarget;
        const text = button.dataset.copy;
        try {
            await this.waitFor(navigator.clipboard.writeText(text));
        } catch {
            const input = button.parentElement.querySelector('input');
            input?.select();
            document.execCommand('copy');
        }
        const label = button.textContent;
        button.textContent = _t("Copiado!");
        this.waitForTimeout(() => (button.textContent = label), 2000);
    }
}

registry
    .category('public.interactions')
    .add('payment_asaas.transaction_instructions', AsaasTransactionInstructions);

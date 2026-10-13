import { patch } from '@web/core/utils/patch';

import { PaymentPostProcessing } from '@payment/interactions/post_processing';

patch(PaymentPostProcessing, {
    /**
     * Pix e boleto ficam pendentes até o comprador pagar: a página de status segue
     * para a página de destino (confirmação do pedido, fatura, pedido no portal), que
     * mostra como pagar, em vez de girar.
     *
     * @override method from `@payment/interactions/post_processing`
     * @param {string} providerCode - O código do provedor da transação.
     */
    getFinalStates(providerCode) {
        const finalStates = super.getFinalStates(...arguments);
        if (providerCode === 'asaas') {
            finalStates.add('pending');
        }
        return finalStates;
    },
});

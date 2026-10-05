/** @odoo-module **/

import paymentPostProcessing from '@payment/js/post_processing';

paymentPostProcessing.include({
    /**
     * Pix e boleto ficam pendentes até o comprador pagar: a página de status segue
     * para a confirmação do pedido, que mostra como pagar, em vez de girar.
     */
    _getFinalStates(providerCode) {
        const finalStates = this._super(...arguments);
        if (providerCode === 'asaas') {
            finalStates.add('pending');
        }
        return finalStates;
    },
});

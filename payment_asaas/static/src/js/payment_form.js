/** @odoo-module **/

import { _t } from '@web/core/l10n/translation';
import { rpc, RPCError } from '@web/core/network/rpc';
import paymentForm from '@payment/js/payment_form';

/**
 * Checkout transparente do Asaas: Pix, boleto e cartão sem sair do site.
 *
 * O Odoo cria a transação; esta camada chama `/payment/asaas/charge`, que gera a
 * cobrança no Asaas. Cartão volta pago ou recusado na hora; Pix e boleto seguem
 * para a confirmação do pedido, que mostra o QR Code ou a linha digitável.
 */
paymentForm.include({
    events: Object.assign({}, paymentForm.prototype.events, {
        'input input[name="asaas_card_number"]': '_asaasFormatCardNumber',
        'input input[name="asaas_card_expiry"]': '_asaasFormatExpiry',
    }),

    async _prepareInlineForm(providerId, providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'asaas') {
            return this._super(...arguments);
        }
        if (flow === 'token') {
            return;
        }
        this._setPaymentFlow('direct');
    },

    async _initiatePaymentFlow(providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode === 'asaas' && flow === 'direct' && paymentMethodCode === 'card') {
            const error = this._asaasValidateCard(this._asaasCardValues());
            if (error) {
                this._displayErrorDialog(_t("Confira os dados do cartão"), error);
                this._enableButton();
                return;
            }
        }
        return this._super(...arguments);
    },

    async _processDirectFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'asaas') {
            return this._super(...arguments);
        }
        const params = {
            reference: processingValues.reference,
            access_token: processingValues.asaas_access_token,
        };
        if (paymentMethodCode === 'card') {
            params.card = this._asaasCardValues();
        }
        try {
            const result = await rpc('/payment/asaas/charge', params);
            if (result.error) {
                this._displayErrorDialog(_t("Pagamento não concluído"), result.error);
                this._enableButton();
                return;
            }
            window.location = '/payment/status';
        } catch (error) {
            if (error instanceof RPCError) {
                this._displayErrorDialog(_t("Pagamento não concluído"), error.data.message);
                this._enableButton();
            } else {
                throw error;
            }
        }
    },

    // #=== CARTÃO ===#

    _asaasCardForm() {
        const radio = this.el.querySelector('input[name="o_payment_radio"]:checked');
        return radio && this._getInlineForm(radio)?.querySelector('.o_asaas_card_form');
    },

    _asaasCardValues() {
        const form = this._asaasCardForm();
        const value = (name) => form?.querySelector(`[name="${name}"]`)?.value.trim() || '';
        return {
            number: value('asaas_card_number'),
            holder: value('asaas_card_holder'),
            expiry: value('asaas_card_expiry'),
            cvv: value('asaas_card_cvv'),
            holder_document: value('asaas_card_document'),
        };
    },

    _asaasValidateCard(card) {
        const digits = card.number.replace(/\D/g, '');
        if (digits.length < 13 || digits.length > 19) {
            return _t("Número do cartão incompleto.");
        }
        if (!card.holder) {
            return _t("Informe o nome impresso no cartão.");
        }
        const match = card.expiry.match(/^(\d{1,2})\s*\/\s*(\d{2}|\d{4})$/);
        if (!match || Number(match[1]) < 1 || Number(match[1]) > 12) {
            return _t("Validade no formato MM/AA.");
        }
        if (!/^\d{3,4}$/.test(card.cvv)) {
            return _t("CVV com 3 ou 4 dígitos.");
        }
        return '';
    },

    _asaasFormatCardNumber(ev) {
        const digits = ev.target.value.replace(/\D/g, '').slice(0, 19);
        ev.target.value = digits.replace(/(\d{4})(?=\d)/g, '$1 ');
    },

    _asaasFormatExpiry(ev) {
        const digits = ev.target.value.replace(/\D/g, '').slice(0, 4);
        ev.target.value = digits.length > 2 ? `${digits.slice(0, 2)}/${digits.slice(2)}` : digits;
    },
});

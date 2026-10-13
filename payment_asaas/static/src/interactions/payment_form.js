import { _t } from '@web/core/l10n/translation';
import { rpc, RPCError } from '@web/core/network/rpc';
import { patch } from '@web/core/utils/patch';

import { PaymentForm } from '@payment/interactions/payment_form';

/**
 * Checkout transparente do Asaas: Pix, boleto e cartão sem sair do site.
 *
 * O Odoo cria a transação; esta camada chama `/payment/asaas/charge`, que gera a
 * cobrança no Asaas. Cartão volta pago ou recusado na hora; Pix e boleto seguem
 * para a página de destino (confirmação do pedido, fatura...), que mostra o QR Code
 * ou a linha digitável.
 */
patch(PaymentForm.prototype, {

    setup() {
        super.setup();
        // Máscaras do cartão. Os campos já estão na página (formulário inline renderizado
        // pelo servidor), então entram no conteúdo dinâmico antes de ele ser processado.
        Object.assign(this.dynamicContent, {
            'input[name="asaas_card_number"]': { 't-on-input': this._asaasFormatCardNumber },
            'input[name="asaas_card_expiry"]': { 't-on-input': this._asaasFormatExpiry },
        });
    },

    // #=== DOM MANIPULATION ===#

    /**
     * @override method from `@payment/interactions/payment_form`
     */
    async _prepareInlineForm(providerId, providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'asaas') {
            await super._prepareInlineForm(...arguments);
            return;
        }
        if (flow === 'token') {
            return;
        }
        this._setPaymentFlow('direct');
        if (paymentMethodCode === 'card') {
            this._asaasFillInstallments();
        }
    },

    // #=== PAYMENT FLOW ===#

    /**
     * Confere o CPF/CNPJ e o cartão antes de criar a transação.
     *
     * @override method from `@payment/interactions/payment_form`
     */
    async _initiatePaymentFlow(providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'asaas') {
            await super._initiatePaymentFlow(...arguments);
            return;
        }
        const documentInput = this._asaasDocumentInput();
        if (documentInput && ![11, 14].includes(documentInput.value.replace(/\D/g, '').length)) {
            this._displayErrorDialog(
                _t("Falta o CPF ou CNPJ"),
                _t("Informe o CPF (11 dígitos) ou o CNPJ (14 dígitos) de quem paga."),
            );
            this._enableButton();
            return;
        }
        if (flow === 'direct' && paymentMethodCode === 'card') {
            const error = this._asaasValidateCard(this._asaasCardValues());
            if (error) {
                this._displayErrorDialog(_t("Confira os dados do cartão"), error);
                this._enableButton();
                return;
            }
        }
        await super._initiatePaymentFlow(...arguments);
    },

    /**
     * Cria a cobrança no Asaas pela rota do módulo, com o token da transação.
     *
     * @override method from `@payment/interactions/payment_form`
     */
    async _processDirectFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'asaas') {
            await super._processDirectFlow(...arguments);
            return;
        }
        const params = {
            reference: processingValues.reference,
            access_token: processingValues.asaas_access_token,
        };
        if (paymentMethodCode === 'card') {
            params.card = this._asaasCardValues();
        }
        const documentInput = this._asaasDocumentInput();
        if (documentInput) {
            params.document = documentInput.value.trim();
        }
        try {
            const result = await this.waitFor(rpc('/payment/asaas/charge', params));
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

    // #=== ASAAS ===#

    /** Formulário inline do método escolhido. */
    _asaasInlineForm() {
        const radio = this.el.querySelector('input[name="o_payment_radio"]:checked');
        return radio && this._getInlineForm(radio);
    },

    /** Campo de CPF/CNPJ do método escolhido (só existe quando o cadastro não tem). */
    _asaasDocumentInput() {
        return this._asaasInlineForm()?.querySelector('[name="asaas_payer_document"]');
    },

    _asaasCardForm() {
        return this._asaasInlineForm()?.querySelector('.o_asaas_card_form');
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
            installments: value('asaas_card_installments') || '1',
        };
    },

    /** Opções de parcelas para o valor a pagar ("3x de R$ 100,00 sem juros"). */
    _asaasFillInstallments() {
        const form = this._asaasCardForm();
        const select = form?.querySelector('[name="asaas_card_installments"]');
        if (!select) {
            return;
        }
        const max = parseInt(form.dataset.maxInstallments || '1', 10);
        const minimum = parseFloat(form.dataset.minInstallment || '5');
        const amount = parseFloat(this.paymentContext.amount || '0');
        const money = (value) => value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
        select.replaceChildren();
        const addOption = (value, label) => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = label;
            select.appendChild(option);
        };
        addOption('1', `${_t("À vista")} (${money(amount)})`);
        for (let count = 2; count <= max; count++) {
            if (amount / count < minimum) {
                break;
            }
            addOption(String(count), `${count}x ${_t("de")} ${money(amount / count)} ${_t("sem juros")}`);
        }
        select.closest('.o_asaas_installments').classList.toggle('d-none', select.options.length === 1);
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

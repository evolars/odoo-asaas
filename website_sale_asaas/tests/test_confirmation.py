import json

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "website_sale_asaas")
class TestConfirmation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brl = cls.env.ref("base.BRL")
        cls.brl.active = True
        cls.provider = cls.env.ref("payment_asaas.payment_provider_asaas")
        cls.provider.write({"asaas_api_key": "$aact_hmlg_test", "state": "test"})
        cls.partner = cls.env["res.partner"].create({
            "name": "Aluna Teste", "vat": "529.982.247-25", "email": "aluna@example.com",
        })
        product = cls.env["product.product"].create({
            "name": "Curso de Odoo", "type": "service", "list_price": 197.0,
        })
        pricelist = cls.env["product.pricelist"].create({
            "name": "BRL", "currency_id": cls.brl.id,
        })
        cls.order = cls.env["sale.order"].create({
            "partner_id": cls.partner.id,
            "pricelist_id": pricelist.id,
            "website_id": cls.env["website"].search([], limit=1).id,
            "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1})],
        })

    def _render(self):
        return str(self.env["ir.qweb"]._render("website_sale.payment_confirmation_status", {
            "order": self.order, "order_tracking_info": {}, "json": json,
        }))

    def test_pending_pix_is_shown_on_the_shop_confirmation(self):
        tx = self.env["payment.transaction"].create({
            "provider_id": self.provider.id,
            "payment_method_id": self.env.ref("payment.payment_method_pix").id,
            "partner_id": self.partner.id,
            "amount": self.order.amount_total,
            "currency_id": self.order.currency_id.id,
            "reference": self.env["payment.transaction"]._compute_reference("asaas"),
            "sale_order_ids": [(6, 0, self.order.ids)],
            "asaas_pix_payload": "00020126pixcurso",
            "asaas_pix_qr_code": "iVBORw0KGgo=",
        })
        tx._set_pending()
        html = self._render()
        self.assertIn("o_asaas_instructions", html)
        self.assertIn("00020126pixcurso", html)

        tx._set_done()
        self.assertNotIn("00020126pixcurso", self._render(), "pago, não mostra mais como pagar")

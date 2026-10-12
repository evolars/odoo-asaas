from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Fatura, forma e situação das cobranças criadas antes desses campos."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["payment.transaction"].search([
        ("provider_code", "=", "asaas"),
        ("provider_reference", "!=", False),
        ("asaas_status", "=", False),
    ])._asaas_fetch_charge_info()

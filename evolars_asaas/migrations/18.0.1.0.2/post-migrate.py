"""O Usuário interno deixa de implicar Asaas Usuário (antigo ADR-019).

Tirar o registro do XML não desfaz o vínculo já gravado no banco; é aqui que ele sai.
Quem já tinha o grupo continua com ele: retirar acessos é decisão de cada empresa.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    asaas_user = env.ref("evolars_asaas.group_asaas_user", raise_if_not_found=False)
    if asaas_user:
        env.ref("base.group_user").write({"implied_ids": [(3, asaas_user.id)]})

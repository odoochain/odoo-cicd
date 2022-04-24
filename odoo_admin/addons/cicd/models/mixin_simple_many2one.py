from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class SimpleMany2one(models.AbstractModel):
    _name = 'cicd.mixin.simple.many2one'

    name = fields.Char("Name", required=True)

    def ensure_exists(self, name):
        existing = self.search(
            [('name', '=', name)], limit=1, order='id desc')
        if not existing:
            return self.create({'name': name})
        return existing


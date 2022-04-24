from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class BranchEpic(models.Model):
    _inherit = ['cicd.mixin.simple.many2one']
    _name = 'cicd.branch.epic'

    name = fields.Char("Epic")
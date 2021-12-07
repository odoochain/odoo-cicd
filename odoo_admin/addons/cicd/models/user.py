from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class User(models.Model):
    _inherit = 'res.users'

    debug_mode_in_instances = fields.Boolean("Debug Mode in odoo instances")

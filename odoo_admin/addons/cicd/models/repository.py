from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from . import lib_git_fetch
from . import lib_get_fetch
class Repository(models.Model):
    _name = 'cicd.git.repo'

    name = fields.Char("URL", required=True)
    login_type = fields.Selection([
        ('username', 'Username'),
        ('key', 'Key'),
    ])
    key = fields.Text("Key")
    username = fields.Char("Username")
    password = fields.Char("Password")


    _sql_constraints = [
        ('name_unique', "unique(named)", _("Only one unique entry allowed.")),
    ]

    @api.model
    def _cron_fetch(self):
        for repo in self.search([]):
            lib_git_fetch._make_new_instances(self)

    @api.model
    def _cron_git_state(self):
        for repo in self.search([]):
            lib_git_fetch._git_state(self)

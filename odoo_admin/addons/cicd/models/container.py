from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
class DockerContainer(models.Model):
    _name = 'docker.container'

    name = fields.Char("Name", required=True)
    state = fields.Selection([('up', 'Up'), ('down', 'Down')], string="State")
    state_string = fields.Char("State (from shell)")
    branch_id = fields.Many2one('cicd.git.branch', string="Branch")
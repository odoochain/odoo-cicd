from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class MakeDump(models.TransientModel):
    _name = 'cicd.wiz.make_snapshot'

    branch_id = fields.Many2one(
        'cicd.git.branch', string="Branch", required=True)
    name = fields.Char("Name", required=True)

    def make_snapshot(self):
        for rec in self:
            with rec.branch_id.shell(logs_title="make_snapshot") as shell:
                shell.odoo('snap', 'save', rec.name)
        return {'type': 'ir.actions.act_window_close'}
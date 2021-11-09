import os
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from pathlib import Path
import humanize

class Dump(models.Model):
    _inherit = ['cicd.mixin.size']
    _name = 'cicd.dump'

    active = fields.Boolean("Active", default=True)
    name = fields.Char("Name", required=True)
    machine_id = fields.Many2one("cicd.machine", string="Machine", required=True)

    @api.constrains("name")
    def _check_name(self):
        for rec in self:
            while rec.name.endswith("/"):
                rec.name = rec.name[:-1]

    def _update_dumps(self, machine):

        with machine._shell() as shell:
            for volume in machine.volume_ids.filtered(lambda x: x.contains_dumps):
                files = machine._execute_shell([
                    "ls", volume.name + "/"
                ]).split("\n")

                import pudb;pudb.set_trace()
                for file in files:
                    path = volume.name + "/" + file
                    fileobj = shell.open(path)
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from pathlib import Path
import humanize

class Dump(models.Model):
    _inherit = ['cicd.mixin.size']
    _name = 'cicd.dump'

    name = fields.Char("Name")
    machine_id = fields.Many2one("cicd.machine", string="Machine")

    def _update_dumps(self, machine):
        import pudb;pudb.set_trace()
        for path in machine.split(","):
            stdout = machine._execute_shell([
                "ls", "-l", path
            ])

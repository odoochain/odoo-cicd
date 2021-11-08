from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
import humanize
from ..tools.tools import _execute_shell

class CicdMachine(models.Model):
    _name = 'cicd.machine'

    volume_ids = fields.One2many("cicd.machine.volume", 'machine_id', string="Volumes")

class CicdVolumes(models.Model):
    _name = 'cicd.machine.volume'

    name = fields.Char("Path")
    size = fields.Integer("Size")
    size_human = fields.Char("Size", compute="_humanize")
    machine_id = fields.Many2one('cicd.machine', string="Machine")

    @api.depends('size')
    def _humanize(self):
        for rec in self:
            rec.size_human = humanize.naturalsize(rec.size)

    def update_values(self):
        res, stdout, stderr = _execute_shell(["/usr/bin/df", '-h', '/'])


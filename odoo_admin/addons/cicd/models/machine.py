from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
import humanize
from ..tools.tools import _execute_shell
import logging
logger = logging.getLogger(__name__)

class CicdMachine(models.Model):
    _name = 'cicd.machine'

    name = fields.Char("Name")
    volume_ids = fields.One2many("cicd.machine.volume", 'machine_id', string="Volumes")
    ssh_user = fields.Char("SSH User")
    ssh_pubkey = fields.Text("SSH Pubkey", compute="_compute_pubkey")

    def _compute_pubkey(self):
        for rec in self:
            rec.ssh_pubkey = ''

    @api.model
    def create(self, vals):
        res = super().create(vals)
        if len(self.search([])) > 1:
            raise ValidationError(_("Maximum one machine support!"))
        return res

    def _execute_shell(self, cmd):
        res, stdout, stderr = _execute_shell(cmd)
        if stderr:
            raise Exception(stderr)
        return stdout
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
        try:
            stdout = self.machine_id._execute_shell(["/usr/bin/df", '-h', '/'])
        except Exception as ex:
            logger.error(ex)
        else:
            import pudb;pudb.set_trace()
            self.size = 1

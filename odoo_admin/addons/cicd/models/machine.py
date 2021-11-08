from odoo import _, api, fields, models, SUPERUSER_ID
import tempfile
import subprocess
from odoo.exceptions import UserError, RedirectWarning, ValidationError
import humanize
from ..tools.tools import _execute_shell
from ..tools.tools import tempdir
import logging
logger = logging.getLogger(__name__)

class CicdMachine(models.Model):
    _name = 'cicd.machine'

    name = fields.Char("Name")
    volume_ids = fields.One2many("cicd.machine.volume", 'machine_id', string="Volumes")
    ssh_user = fields.Char("SSH User")
    ssh_pubkey = fields.Text("SSH Pubkey")
    ssh_key = fields.Text("SSH Key")

    def generate_ssh_key(self):
        self.ensure_one()
        with tempdir() as dir:
            subprocess.check_call([
                '/usr/bin/ssh-keygen', '-f', 'temp',
                '-P', ''
            ], cwd=dir)
            keyfile = dir / 'temp'
            pubkeyfile = dir / 'temp.pub'
            self.ssh_key = keyfile.read_text()
            self.ssh_pubkey = pubkeyfile.read_text()

    @api.model
    def create(self, vals):
        res = super().create(vals)
        if len(self.search([])) > 1:
            raise ValidationError(_("Maximum one machine support!"))
        return res

    def test_ssh(self):
        self._execute_shell(["ls"])

    def _execute_shell(self, cmd):
        res, stdout, stderr = _execute_shell(self, cmd)
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

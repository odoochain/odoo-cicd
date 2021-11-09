import os
import pwd
import grp
from pathlib import Path
import spur
from contextlib import contextmanager
from odoo import _, api, fields, models, SUPERUSER_ID
import tempfile
import paramiko
import subprocess
from odoo.exceptions import UserError, RedirectWarning, ValidationError
import humanize
from ..tools.tools import _execute_shell
from ..tools.tools import tempdir
from ..tools.tools import get_host_ip
import logging
logger = logging.getLogger(__name__)

class CicdMachine(models.Model):
    _name = 'cicd.machine'

    name = fields.Char("Name")
    is_docker_host = fields.Boolean("Is Docker Host", default=True)
    host = fields.Char("Host")
    volume_ids = fields.One2many("cicd.machine.volume", 'machine_id', string="Volumes")
    ssh_user = fields.Char("SSH User")
    ssh_pubkey = fields.Text("SSH Pubkey")
    ssh_key = fields.Text("SSH Key")
    dump_paths = fields.Char("Dump Paths")
    dump_ids = fields.One2many('cicd.dump', 'machine_id', string="Dumps")
    effective_host = fields.Char(compute="_compute_effective_host", store=False)

    def _compute_effective_host(self):
        for rec in self:
            if rec.is_docker_host:
                rec.effective_host = get_host_ip()
            else:
                rec.effective_host = rec.host

    def _place_ssh_credentials(self):
        self.ensure_one()
        # place private keyfile
        ssh_dir = Path(os.path.expanduser("~/.ssh"))
        ssh_dir.mkdir(exist_ok=True)
        os.chown(ssh_dir, pwd.getpwnam('odoo').pw_uid, grp.getgrnam('odoo').gr_gid)
        os.chmod(ssh_dir, 0o700)

        ssh_keyfile = ssh_dir / self.effective_host
        rights_keyfile = 0o600
        if ssh_keyfile.exists():
            os.chmod(ssh_keyfile, rights_keyfile)
        ssh_keyfile.write_text(self.ssh_key)
        os.chmod(ssh_keyfile, rights_keyfile)
        return ssh_keyfile

    @contextmanager
    def _shell(self):
        self.ensure_one()
        ssh_keyfile = self._place_ssh_credentials()
        with spur.SshShell(
            hostname=get_host_ip(),
            username=self.ssh_user,
            private_key_file=str(ssh_keyfile),
            missing_host_key=spur.ssh.MissingHostKey.accept
            ) as shell:
            yield shell


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
        raise ValidationError(_("Everyhing Works!"))

    def _execute_shell(self, cmd, cwd=None, env=None, callback=None):
        res, stdout, stderr = _execute_shell(self, cmd, cwd=cwd, env=env, callback=callback)
        if stderr:
            raise Exception(stderr)
        return stdout

    def update_dumps(self):
        self.ensure_one()
        self.env['cicd.dump']._update_dumps(self)

class CicdVolumes(models.Model):
    _inherit = ['cicd.mixin.size']
    _name = 'cicd.machine.volume'

    name = fields.Char("Path")
    machine_id = fields.Many2one('cicd.machine', string="Machine")
    contains_dumps = fields.Boolean("Contains Dumps")
    used_size_human = fields.Char("Used Size", compute="_compute_numbers")
    free_size_human = fields.Char("Free Size", compute="_compute_numbers")
    total_size_human = fields.Char("Total Size", compute="_compute_numbers")
    used_size = fields.Integer("Used Size", compute="_compute_numbers")
    free_size = fields.Integer("Free Size", compute="_compute_numbers")
    total_size = fields.Integer("Total Size", compute="_compute_numbers")
    used_percent = fields.Float("Used %", compute="_compute_numbers")

    def update_values(self):
        try:
            stdout = self.machine_id._execute_shell(["/usr/bin/df", '-h', '/'])
        except Exception as ex:
            logger.error(ex)
        else:
            import pudb;pudb.set_trace()
            self.size = 1

    def update_sizes(self):
        for rec in self:
            rec.machine
            with rec._shell() as shell:
                stdout = rec.machine_id._execute_shell([
                    "df", rec.name
                ])
                import pudb;pudb.set_trace()

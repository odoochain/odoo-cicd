import os
import pwd
import grp
from pathlib import Path
import spur
import spurplus
from contextlib import contextmanager
from odoo import _, api, fields, models, SUPERUSER_ID
import tempfile
import paramiko
import subprocess
from odoo.exceptions import UserError, RedirectWarning, ValidationError
import humanize
from ..tools.tools import tempdir
from ..tools.tools import get_host_ip
import logging
logger = logging.getLogger(__name__)

class ShellExecutor(object):
    def __init__(self, machine, cwd, logsio, env):
        self.machine = machine
        self.cwd = cwd
        self.logsio = logsio
        self.env = env
    def X(self, cmd):
        return self.machine._execute_shell(
            cmd, cwd=self.cwd, env=self.env, logsio=self.logsio
        )

class CicdMachine(models.Model):
    _name = 'cicd.machine'

    name = fields.Char("Name")
    is_docker_host = fields.Boolean("Is Docker Host", default=True)
    host = fields.Char("Host")
    volume_ids = fields.One2many("cicd.machine.volume", 'machine_id', string="Volumes")
    ssh_user = fields.Char("SSH User")
    ssh_pubkey = fields.Text("SSH Pubkey", readonly=True)
    ssh_key = fields.Text("SSH Key")
    dump_ids = fields.One2many('cicd.dump', 'machine_id', string="Dumps")
    effective_host = fields.Char(compute="_compute_effective_host", store=False)
    workspace = fields.Char("Workspace", compute="_compute_workspace")
    ttype = fields.Selection([
        ('dev', 'Development-Machine'),
        ('prod', 'Production System'),
    ], required=True)

    def _compute_workspace(self):
        for rec in self:
            rec.workspace = os.environ['CONTAINER_CICD_WORKSPACE']

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
        with spurplus.connect_with_retries(
            hostname=get_host_ip(),
            username=self.ssh_user,
            private_key_file=str(ssh_keyfile),
            missing_host_key=spur.ssh.MissingHostKey.accept,
            ) as shell:
            yield shell

    @contextmanager
    def _shellexec(self, cwd, logsio, env=None):
        self.ensure_one()
        executor = ShellExecutor(self, cwd, logsio, env or {})
        yield executor

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

    def _execute_shell(self, cmd, cwd=None, env=None, logsio=None):

        class MyWriter(object):
            def __init__(self, ttype):
                self.text = [""]
                self.ttype = ttype
                self.line = ""

            def finish(self):
                self._write_line()

            def write(self, text):
                if not logsio:
                    return
                if '\n' in text and len(text) == 1:
                    self._write_line()
                    self.line = ""
                else:
                    self.line += text
                    return

            def _write_line(self):
                if not self.line:
                    return
                if self.ttype == 'error':
                    logsio.error(self.line)
                else:
                    logsio.info(self.line)

        with self._shell() as shell:
            stdwriter, errwriter = MyWriter('info'), MyWriter('error')

            res = shell.run(
                cmd, cwd=cwd, update_env=env or {},
                stdout=stdwriter, stderr=errwriter,
            )
            stdwriter.finish()
            errwriter.finish()
            return res

    def update_dumps(self):
        for rec in self:
            rec.env['cicd.dump']._update_dumps(rec)

    def update_volumes(self):
        self.mapped('volume_ids')._update_sizes()

    def update_all_values(self):
        self.update_dumps()
        self.update_volumes()

    def _get_sshuser_id(self):
        user_name = self.ssh_user
        res = self._execute_shell(self, ["/usr/bin/id", '-u', user_name])
        user_id = res.output.strip()
        return user_id

    def _get_volume(self, ttype):
        res = self.volume_ids.filtered(lambda x: x.ttype == ttype)
        if not res:
            raise ValidationError(_("Could not find: {}").format(ttype))
        return Path(res[0].name)
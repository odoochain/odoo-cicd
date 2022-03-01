import base64
import json
import subprocess
import tempfile
import arrow
from copy import deepcopy
import os
import pwd
import grp
import hashlib
from pathlib import Path
from ..tools.logsio_writer import LogsIOWriter
from contextlib import contextmanager
from odoo import _, api, fields, models, SUPERUSER_ID, tools
import subprocess
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools.tools import tempdir
from ..tools.tools import get_host_ip
from .shell_executor import ShellExecutor
import logging
logger = logging.getLogger(__name__)

class CicdMachine(models.Model):
    _inherit = 'mail.thread'
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
    reload_config = fields.Text("Settings")
    external_url = fields.Char("External http-Address")

    ssh_user_cicdlogin = fields.Char(compute="_compute_ssh_user_cicd_login")
    ssh_user_cicdlogin_password_salt = fields.Char(compute="_compute_ssh_user_cicd_login", store=True)
    ssh_user_cicdlogin_password = fields.Char(compute="_compute_ssh_user_cicd_login")
    postgres_server_id = fields.Many2one('cicd.postgres', string="Postgres Server", required=False)
    upload_dump = fields.Binary("Upload Dump")
    upload_dump_filename = fields.Char("Filename")
    upload_overwrite = fields.Boolean("Overwrite existing")
    upload_volume_id = fields.Many2one('cicd.machine.volume', "Upload Volume", domain=[('ttype', '=', 'dumps')])
    test_timeout_web_login = fields.Integer("Timeout Test Weblogin", default=10, required=True)
    container_states = fields.Text("Json")
    tempfile_containers = fields.Char(compute="compute_tempfile_containers")

    @api.depends('ssh_user')
    def _compute_ssh_user_cicd_login(self):
        for rec in self:
            rec.ssh_user_cicdlogin = (self.ssh_user or '') + "_restricted_cicdlogin"
            if not rec.ssh_user_cicdlogin_password_salt:
                rec.ssh_user_cicdlogin_password_salt = str(arrow.get())
            ho = hashlib.md5((rec.ssh_user_cicdlogin + self.ssh_user_cicdlogin_password_salt).encode('utf-8'))
            rec.ssh_user_cicdlogin_password = ho.hexdigest()

    def _compute_workspace(self):
        for rec in self:
            rec.workspace = rec.volume_ids.filtered(lambda x: x.ttype == 'source').name

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        return res

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
        ssh_pubkeyfile = ssh_dir / (self.effective_host + '.pub')
        rights_keyfile = 0o600
        for file in [
            ssh_keyfile, ssh_pubkeyfile,
        ]:
            if file.exists():
                os.chmod(file, rights_keyfile)
        def content_differs(file, content):
            if not file.exists():
                return True
            return file.read_text() != content

        if content_differs(ssh_keyfile, self.ssh_key):
            ssh_keyfile.write_text(self.ssh_key)
        if content_differs(ssh_pubkeyfile, self.ssh_pubkey):
            ssh_pubkeyfile.write_text(self.ssh_pubkey)
        os.chmod(ssh_keyfile, rights_keyfile)
        os.chmod(ssh_pubkeyfile, rights_keyfile)
        return ssh_keyfile

    def test_shell(self, cmd, cwd=None, env={}):
        with self._shell(cwd=cwd, env=env) as shell:
            return shell.X(cmd, cwd=cwd, env=env)
    def test_shell_exists(self, path):
        with self._shell() as shell:
            return shell.exists(path)

    @contextmanager
    def _shell(self, cwd=None, logsio=None, project_name=None, env={}):
        self.ensure_one()
        ssh_keyfile = self._place_ssh_credentials()

        shell = ShellExecutor(
            ssh_keyfile=ssh_keyfile, machine=self, cwd=cwd,
            logsio=logsio, project_name=project_name, env=env
        )
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

    def test_ssh(self):
        with self._shell() as shell:
            shell.X(["ls"])
        raise ValidationError(_("Everyhing Works!"))

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
        with self._shell() as shell:
            res = shell.X(["/usr/bin/id", '-u', user_name])
        user_id = res['stdout'].strip()
        return user_id

    def _get_volume(self, ttype):
        res = self.volume_ids.filtered(lambda x: x.ttype == ttype)
        if not res:
            raise ValidationError(_("Could not find: {}").format(ttype))
        return Path(res[0].name)

    def springclean(self, **args):
        """
        Removes all unused source directories, databases
        and does a docker system prune.
        """
        with LogsIOWriter.GET(self.name, 'spring_clean') as logsio:
            with self._shell(cwd="~", logsio=logsio) as shell:
                shell.X(["/usr/bin/docker", "system", "prune", "-f"])

    def make_login_possible_for_webssh_container(self):
        pubkey = Path("/opt/cicd_sshkey/id_rsa.pub").read_text().strip()
        for rec in self:
            with rec._shell() as shell:

                command_file = '/tmp/commands.cicd'
                homedir = '/home/' + rec.ssh_user_cicdlogin
                test_file_if_required = homedir + '/.setup_login_done.v3'
                user_upper = rec.ssh_user_cicdlogin.upper()

                # allow per sudo execution of just the odoo script
                commands = """
#!/bin/bash

#------------------------------------------------------------------------------
# adding sudoer command for restricted user to odoo framework

tee "/etc/sudoers.d/{rec.ssh_user_cicdlogin}_odoo" <<EOF
Cmnd_Alias ODOO_COMMANDS_{user_upper} = /usr/local/sbin/odoo *
{rec.ssh_user_cicdlogin} ALL=({rec.ssh_user}) NOPASSWD:SETENV: ODOO_COMMANDS_{user_upper}
EOF

#------------------------------------------------------------------------------
# setting up login to restricted user

grep -q "{rec.ssh_user_cicdlogin}" /etc/passwd || adduser --disabled-password --gecos "" {rec.ssh_user_cicdlogin}
mkdir -p ~/.ssh
chmod 700 ~/.ssh
grep -q "{pubkey}" ~/.ssh/authorized_keys || echo "\n{pubkey}" >> ~/.ssh/authorized_keys
usermod --shell /bin/rbash "{rec.ssh_user_cicdlogin}"

#------------------------------------------------------------------------------
# adding programs to restricted user

mkdir -p "{homedir}/programs"
echo 'readonly PATH={homedir}/programs' > "{homedir}/.bash_profile"
echo 'export PATH' >> "{homedir}/.bash_profile"
chown -R "{rec.ssh_user_cicdlogin}":"{rec.ssh_user_cicdlogin}" "{homedir}"
ln -sf /usr/bin/sudo "{homedir}/programs/sudo"
ln -sf /usr/bin/tmux "{homedir}/programs/tmux"
ln -sf /usr/bin/rbash "{homedir}/programs/rbash"

#------------------------------------------------------------------------------
# setting username / password
echo -e "{rec.ssh_user_cicdlogin_password}\n{rec.ssh_user_cicdlogin_password}" | passwd "{rec.ssh_user_cicdlogin}"

#------------------------------------------------------------------------------
# adding wrapper for calling odoo framework in that instance directory
#!/bin/bash
tee "{homedir}/programs/odoo" <<EOF
sudo -u {rec.ssh_user} /usr/local/sbin/odoo --chdir "\$CICD_WORKSPACE/\$PROJECT_NAME" -p "\$PROJECT_NAME" "\$@"
EOF
chmod a+x "{homedir}/programs/odoo"

#------------------------------------------------------------------------------
# make indication file, that it is setup
echo '1' > '{test_file_if_required}'

#------------------------------------------------------------------------------
# self destruct
rm {command_file}

#------------------------------------------------------------------------------
# give calming success message to admin
echo "------------------------------------------------------------------------------------"
echo ""
echo "Successfully allowing restricted bash access from docker container to only execute odoo framework."
echo "Care is taken, that system cannot be compromised."
echo ""
echo "------------------------------------------------------------------------------------"

                """.format(**locals())
                # in this path there ar, the keys that are used by web ssh container /opt/cicd_sshkey
                if not shell.exists(test_file_if_required):
                    shell.put(commands.strip() + "\n", command_file)
                    cmd = ["sudo", "/bin/bash", command_file]
                    res = shell.X(cmd, allow_error=True)
                    if res['exit_code']:
                        raise UserError(f"Failed to setup restrict login. Please execute on host:\n{' '.join(cmd)}\n\nException:\n{res['stderr']}")

    def write(self, vals):
        if vals.get('upload_dump'):
            self._upload(vals)
        res = super().write(vals)

        # at create if somebody uploaded....mmhh :)
        for rec in self:
            if rec.upload_dump:
                rec.upload_dump = False
        return res

    def upload(self):
        pass

    def _upload(self, vals):
        content = vals.pop('upload_dump')
        filename = vals.pop('upload_dump_filename')

        if not vals.get('upload_volume_id'):
            vols = self.volume_ids.filtered(lambda x: x.ttype == 'dumps')
            if len(vols) > 1:
                raise ValidationError("Please choose a volume!")
            vol = vols[0]
            del vols
        else:
            vol = self.volume_ids.browse(vals['upload_volume_id'])

        with self._shell(cwd='~', logsio=None) as shell1:
            with shell1.shell() as shell2:
                path = Path(vol.name) / filename
                content = base64.b64decode(content)
                shell2.write_bytes(path, content)
        self.message_post(body="New dump uploaded: " + filename)

        for f in ['upload_volume_id', 'upload_overwrite']:
            if f in vals:
                vals.pop(f)

    @contextmanager
    def _gitshell(self, repo, cwd, logsio, env=None, **kwargs):
        self.ensure_one()
        assert repo._name == 'cicd.git.repo'
        env = env or {}
        env.update({
            "GIT_ASK_YESNO": "false",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH_COMMAND": f'ssh -o Batchmode=yes -o StrictHostKeyChecking=no',
        })
        with self._shell(cwd=cwd, logsio=logsio, env=env) as shell:
            file = Path(tempfile.mktemp(suffix='.'))
            try:
                if repo.login_type == 'key':
                    env['GIT_SSH_COMMAND'] += f'   -i {file}  '
                    shell.put(repo.key, file)
                    shell.X(["chmod", '400', str(file)])
                else:
                    pass

                yield shell

            finally:
                shell.remove(file)

    @contextmanager
    def _put_temporary_file_on_machine(self, logsio, source_path, dest_machine, dest_path, delete_copied_file=True):
        """
        Copies a file from machine1 to machine2; optimized if same machine;
        you should not delete the file
        """
        self.ensure_one()
        if self == dest_machine: # TODO undo
            yield source_path
        else:
            filename = tempfile.mktemp(suffix='.')
            ssh_keyfile = self._place_ssh_credentials()
            ssh_cmd_base = f"ssh -o Batchmode=yes -o StrictHostKeyChecking=no -i"
            subprocess.run([
                "rsync",
                '-e',
                ssh_cmd_base + str(ssh_keyfile),
                "-ar",
                self.ssh_user + "@" + self.effective_host + ":" + source_path,
                filename,
            ])
            try:
                ssh_keyfile = dest_machine._place_ssh_credentials()
                subprocess.run([
                    "rsync",
                    '-e',
                    ssh_cmd_base + str(ssh_keyfile),
                    "-ar",
                    filename,
                    dest_machine.ssh_user + "@" + dest_machine.effective_host + ":" + str(dest_path),
                ])
                try:
                    yield dest_path

                finally:
                    if delete_copied_file:
                        with dest_machine._shell(cwd="", logsio=logsio) as shell:
                            shell.rm(dest_path)

            finally:
                os.unlink(filename)

    def test_ssh_connection(self, tests=20):
        with self._shell() as shell:
            shell.put("hansi", "/tmp/hansi")
            for i in range(tests):
                value = shell.exists("/tmp/hansi")
                print(i, value)
                if not value:
                    raise Exception('should exist')
            shell.rm("/tmp/hansi")
            for i in range(tests):
                value = shell.exists("/tmp/hansi")
                print(i, value)
                if value:
                    raise Exception('should exist')

    def _update_docker_containers(self):
        breakpoint()
        for rec in self:
            with rec._shell() as shell:
                containers = shell.X(["docker", "ps", "-a", "--format", "{{ .Names }}\t{{ .State }}"])['stdout'].strip()
                containers_dict = {}
                for line in containers.split("\n")[1:]:
                    try:
                        container, state = line.split("\t")
                    except:
                        # perhaps no access or so
                        pass
                    containers_dict[container] = state
                path = Path(rec.tempfile_containers)
                path.write_text(json.dumps(containers_dict))

    def _get_containers(self):
        path = Path(self.tempfile_containers)
        if not path.exists():
            self._update_docker_containers()
        if path.exists():
            containers = json.loads(path.read_text())
        else:
            containers = {}
        return containers

    def compute_tempfile_containers(self):
        for rec in self:
            # TODO configurable path sysparameter
            path = Path("/opt/out_dir/docker_states")
            path.mkdir(exist_ok=True, parents=True)
            self.tempfile_containers = f"{path}/{self.env.cr.dbname}.machine.{rec.id}.containers"

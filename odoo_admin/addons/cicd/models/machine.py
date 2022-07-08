import base64
import time
import uuid
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
from contextlib import contextmanager, closing
from odoo import _, api, fields, models, SUPERUSER_ID, tools
import subprocess
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools.tools import tempdir
from ..tools.tools import get_host_ip
from .shell_executor import ShellExecutor
import logging
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF

logger = logging.getLogger(__name__)


class CicdMachine(models.Model):
    _inherit = ["mail.thread"]
    _name = "cicd.machine"

    active = fields.Boolean("Active", default=True)
    name = fields.Char("Name")
    is_docker_host = fields.Boolean("Is Docker Host", default=True)
    homedir = fields.Char("Homedir")
    host = fields.Char("Host")
    volume_ids = fields.One2many("cicd.machine.volume", "machine_id", string="Volumes")
    ssh_user = fields.Char("SSH User")
    ssh_pubkey = fields.Text("SSH Pubkey", readonly=True)
    ssh_key = fields.Text("SSH Key")
    dump_ids = fields.One2many("cicd.dump", "machine_id", string="Dumps")
    effective_host = fields.Char(compute="_compute_effective_host", store=False)
    workspace = fields.Char("Workspace", compute="_compute_workspace")
    ttype = fields.Selection(
        [
            ("dev", "Development-Machine"),
            ("prod", "Production System"),
        ],
        required=True,
    )
    reload_config = fields.Text("Settings")
    external_url = fields.Char("External http-Address")

    ssh_user_cicdlogin = fields.Char(compute="_compute_ssh_user_cicd_login", store=True)
    ssh_user_cicdlogin_password_salt = fields.Char(
        compute="_compute_ssh_user_cicd_login", store=True
    )
    ssh_user_cicdlogin_password = fields.Char(
        compute="_compute_ssh_user_cicd_login", store=True
    )
    postgres_server_id = fields.Many2one(
        "cicd.postgres", string="Postgres Server", required=False
    )
    upload_dump = fields.Binary("Upload Dump")
    upload_dump_filename = fields.Char("Filename")
    upload_overwrite = fields.Boolean("Overwrite existing")
    upload_volume_id = fields.Many2one(
        "cicd.machine.volume", "Upload Volume", domain=[("ttype", "=", "dumps")]
    )
    test_timeout_web_login = fields.Integer(
        "Timeout Test Weblogin", default=10, required=True
    )
    tempfile_containers = fields.Char(compute="compute_tempfile_containers")

    @api.depends("ssh_user")
    def _compute_ssh_user_cicd_login(self):
        for rec in self:
            rec.ssh_user_cicdlogin = (rec.ssh_user or "") + "_restricted_cicdlogin"
            if not rec.ssh_user_cicdlogin_password_salt:
                rec.ssh_user_cicdlogin_password_salt = str(arrow.get())
            ho = hashlib.md5(
                (rec.ssh_user_cicdlogin + rec.ssh_user_cicdlogin_password_salt).encode(
                    "utf-8"
                )
            )
            rec.ssh_user_cicdlogin_password = ho.hexdigest()

    def _compute_workspace(self):
        for rec in self:
            rec.workspace = rec.volume_ids.filtered(lambda x: x.ttype == "source").name

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
        try:
            self.ensure_one()
            with self._extra_env() as machine:
                data = machine.read(["effective_host", "ssh_key", "ssh_pubkey"])[0]
                effective_host = data["effective_host"]
                ssh_key = data["ssh_key"]
                ssh_pubkey = data["ssh_pubkey"]

            # place private keyfile
            ssh_dir = Path(os.path.expanduser("~/.ssh"))
            ssh_dir.mkdir(exist_ok=True)
            os.chown(ssh_dir, pwd.getpwnam("odoo").pw_uid, grp.getgrnam("odoo").gr_gid)
            os.chmod(ssh_dir, 0o700)

            ssh_keyfile = ssh_dir / effective_host
            ssh_pubkeyfile = ssh_dir / (effective_host + ".pub")
            rights_keyfile = 0o600
            for file in [
                ssh_keyfile,
                ssh_pubkeyfile,
            ]:
                if file.exists():
                    os.chmod(file, rights_keyfile)

            def content_differs(file, content):
                if not file.exists():
                    return True
                return file.read_text() != content

            if content_differs(ssh_keyfile, ssh_key):
                ssh_keyfile.write_text(ssh_key)
            if content_differs(ssh_pubkeyfile, ssh_pubkey):
                ssh_pubkeyfile.write_text(ssh_pubkey)
            os.chmod(ssh_keyfile, rights_keyfile)
            os.chmod(ssh_pubkeyfile, rights_keyfile)
            return ssh_keyfile
        except Exception as ex:
            raise Exception(
                "Error at placing credentials on {self.machine_id.name}"
            ) from ex

    def test_shell(self, cmd, cwd=None, env=None):
        env = env or {}
        with self._shell(cwd=cwd, env=env) as shell:
            return shell.X(cmd, cwd=cwd, env=env)

    def test_shell_exists(self, path):
        with self._shell() as shell:
            return shell.exists(path)

    @contextmanager
    def _shell(self, cwd=None, logsio=None, project_name=None, env=None):
        env = env or {}
        self.ensure_one()
        ssh_keyfile = self._place_ssh_credentials()

        # avoid long locking
        with self._extra_env() as machine:
            user = machine.ssh_user
            host = machine.effective_host

        shell = ShellExecutor(
            ssh_keyfile=ssh_keyfile,
            host=host,
            cwd=cwd,
            logsio=logsio,
            project_name=project_name,
            env=env,
            user=user,
            machine=self,
        )
        yield shell

    def generate_ssh_key(self):
        self.ensure_one()
        with tempdir() as dir:
            subprocess.check_call(
                ["/usr/bin/ssh-keygen", "-f", "temp", "-P", ""], cwd=dir
            )
            keyfile = dir / "temp"
            pubkeyfile = dir / "temp.pub"
            self.ssh_key = keyfile.read_text()
            self.ssh_pubkey = pubkeyfile.read_text()

    def test_ssh(self):
        with self._shell() as shell:
            shell.X(["ls"])
        raise ValidationError(_("Everyhing Works!"))

    def update_dumps(self):
        for rec in self:
            rec.env["cicd.dump"]._update_dumps(rec)
            self.env.cr.commit()

    def update_volumes(self):
        self.mapped("volume_ids")._update_sizes()

    def update_all_values(self):
        self.update_dumps()
        self.update_volumes()

    def _get_sshuser_id(self):
        user_name = self.ssh_user
        with self._shell() as shell:
            res = shell.X(["/usr/bin/id", "-u", user_name])
        user_id = res["stdout"].strip()
        return user_id

    def _get_volume(self, ttype):
        with self._extra_env() as x_self:
            res = x_self.volume_ids.filtered(lambda x: x.ttype == ttype)
            if not res:
                raise ValidationError(_("Could not find: {}").format(ttype))
            return Path(res[0].name)

    def _temppath(self, maxage={"hours": 1}, usage="common"):
        guid = str(uuid.uuid4())
        date = arrow.utcnow().shift(**maxage).strftime("%Y%m%d_%H%M%S")
        name = f"{guid}.{usage}.cleanme.{date}"
        return self._get_volume("temp") / name

    @api.model
    def _clean_tempdirs(self):
        for machine in self.search([('active', '=', True)]):
            machine.with_delay()._clean_temppath()

    def _clean_temppath(self):
        breakpoint()
        self.ensure_one()
        if not self.active:
            return
        with self._shell() as shell:
            for vol in self.volume_ids.filtered(lambda x: x.ttype == "temp"):
                if not shell.exists(vol.name):
                    continue
                for dirname in shell.X(["ls", "-l", vol.name])["stdout"].splitlines():
                    if ".cleanme." in dirname:
                        try:
                            date = arrow.get(dirname.split(".")[-1], "YYYYMMDD_HHmmss")
                        except Exception:
                            date = arrow.utcnow().shift(years=-1)

                        if date < arrow.utcnow():
                            shell.remove(vol.name + "/" + dirname)

    def springclean(self, **args):
        """
        Removes all unused source directories, databases
        and does a docker system prune.
        """
        with LogsIOWriter.GET(self.name, "spring_clean") as logsio:
            with self._shell(cwd="~", logsio=logsio) as shell:
                shell.X(["/usr/bin/docker", "system", "prune", "-f"])

    def make_login_possible_for_webssh_container(self):
        pubkey = Path("/opt/cicd_sshkey/id_rsa.pub").read_text().strip()
        pubkey_machine = self.ssh_pubkey
        for rec in self:
            with rec._shell() as shell:

                command_file = "/tmp/commands.cicd"
                homedir = "/home/" + rec.ssh_user_cicdlogin
                test_file_if_required = homedir + "/.setup_login_done.v6"
                user_upper = rec.ssh_user_cicdlogin.upper()
                cicd_user_upper = rec.ssh_user.upper()

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
# allowing cicd user to kil tmux sessions of restricted login

tee "/etc/sudoers.d/{rec.ssh_user}_kill_tmux_allowed" <<EOF
Cmnd_Alias ODOO_COMMANDS_{cicd_user_upper}_KILL_TMUX = /usr/bin/pkill *
{rec.ssh_user} ALL=({rec.ssh_user}) NOPASSWD:SETENV: ODOO_COMMANDS_{cicd_user_upper}_KILL_TMUX
EOF

#------------------------------------------------------------------------------
# setting up login to restricted user

grep -q "{rec.ssh_user_cicdlogin}" /etc/passwd || adduser --disabled-password --gecos "" {rec.ssh_user_cicdlogin}
mkdir -p "{homedir}/.ssh"
chmod 700 "{homedir}/.ssh"
grep -q "{pubkey}" "{homedir}/.ssh/authorized_keys" || echo "\n{pubkey}" >> "{homedir}/.ssh/authorized_keys"
chmod 600 "{homedir}/.ssh/authorized_keys"
grep -q "{pubkey_machine}" "{homedir}/.ssh/authorized_keys" || echo "\n{pubkey_machine}" >> "{homedir}/.ssh/authorized_keys"
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
ln -sf /usr/bin/pkill "{homedir}/programs/pkill"
ln -sf /usr/bin/base64 "{homedir}/programs/base64"
ln -sf /usr/bin/mktemp "{homedir}/programs/mktemp"
ln -sf /usr/bin/rm "{homedir}/programs/rm"
ln -sf /usr/bin/reset "{homedir}/programs/reset"
ln -sf /usr/bin/echo "{homedir}/programs/echo"
ln -sf /usr/bin/sleep "{homedir}/programs/sleep"

#------------------------------------------------------------------------------
tee "{homedir}/programs/start_tmux" <<'EOF'
SESSION_NAME="$1"
PROJECT_NAME="$2"
CICD_WORKSPACE="$3"

tmux has-session -t "$SESSION_NAME"
hasSession=$?

if [[ $hasSession != "0" ]]; then  \\
    tmux new-session -s "$SESSION_NAME" -d
    tmux send-keys -t "$SESSION_NAME.0" "reset" ENTER
    tmux send-keys -t "$SESSION_NAME.0" "echo 'Welcome to CICD Odoo Shell. Type odoo --help to get started.'" ENTER
    tmux send-keys -t "$SESSION_NAME.0" "export PROJECT_NAME=$PROJECT_NAME" ENTER
    tmux send-keys -t "$SESSION_NAME.0" "export CICD_WORKSPACE=$CICD_WORKSPACE" ENTER
    if [[ ! -z "$4" ]]; then
        CMD="$(echo "$4" | base64 -d )"
        tmux send-keys -t "$SESSION_NAME.0" "$CMD" ENTER
    fi
    tmux a -t "$SESSION_NAME"
else
    tmux has-session -t "$SESSION_NAME" && \\
        tmux attach -t "$SESSION_NAME"
fi
EOF
chmod a+x "{homedir}/programs/start_tmux"


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

                """.format(
                    **locals()
                )
                # in this path there ar, the keys that are used by
                # web ssh container /opt/cicd_sshkey
                if not shell.exists(test_file_if_required):
                    shell.put(commands.strip() + "\n", command_file)
                    cmd = ["sudo", "/bin/bash", command_file]
                    res = shell.X(cmd, allow_error=True)
                    if res["exit_code"]:
                        raise UserError(
                            (
                                "Failed to setup restrict login. "
                                "Please execute on host:\n"
                                f"{' '.join(cmd)}\n\n"
                                f"Exception:\n{res['stderr']}"
                            )
                        )

    def write(self, vals):
        if vals.get("upload_dump"):
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
        content = vals.pop("upload_dump")
        filename = vals.pop("upload_dump_filename")

        if not vals.get("upload_volume_id"):
            vols = self.volume_ids.filtered(lambda x: x.ttype == "dumps")
            if len(vols) > 1:
                raise ValidationError("Please choose a volume!")
            vol = vols[0]
            del vols
        else:
            vol = self.volume_ids.browse(vals["upload_volume_id"])

        with self._shell(cwd="~", logsio=None) as shell:
            path = Path(vol.name) / filename
            content = base64.b64decode(content)
            shell.put(content, path)
        self.message_post(body="New dump uploaded: " + filename)

        for f in ["upload_volume_id", "upload_overwrite"]:
            if f in vals:
                vals.pop(f)

    @contextmanager
    def _gitshell(self, repo, cwd, logsio, env=None, **kwargs):
        self.ensure_one()
        assert repo._name == "cicd.git.repo"
        env = env or {}
        env.update(
            {
                "GIT_ASK_YESNO": "false",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_SSH_COMMAND": "ssh -o Batchmode=yes -o StrictHostKeyChecking=no",
            }
        )
        with self._shell(cwd=cwd, logsio=logsio, env=env) as shell:
            file = Path(tempfile.mktemp(suffix="."))
            try:
                if repo._unblocked("login_type") == "key":
                    env["GIT_SSH_COMMAND"] += f"   -i {file}  "
                    shell.put(repo._unblocked("key"), file)
                    shell.X(["chmod", "400", str(file)])
                else:
                    pass

                yield shell

            finally:
                shell.remove(file)

    @contextmanager
    def _put_temporary_file_on_machine(
        self, logsio, source_path, dest_machine, dest_path, delete_copied_file=True
    ):
        """
        Copies a file from machine1 to machine2; optimized if same machine;
        you should not delete the file
        """
        self.ensure_one()
        if self == dest_machine:
            yield source_path
        else:
            filename = tempfile.mktemp(suffix=".") #locally
            ssh_keyfile = self._place_ssh_credentials()
            ssh_cmd_base = f"ssh -o Batchmode=yes -o StrictHostKeyChecking=no -i"
            subprocess.run(
                [
                    "rsync",
                    "-e",
                    ssh_cmd_base + str(ssh_keyfile),
                    "-ar",
                    self.ssh_user + "@" + self.effective_host + ":" + source_path,
                    filename,
                ]
            )
            try:
                ssh_keyfile = dest_machine._place_ssh_credentials()
                subprocess.check_call(
                    [
                        "rsync",
                        "-e",
                        ssh_cmd_base + str(ssh_keyfile),
                        "-ar",
                        filename,
                        dest_machine.ssh_user
                        + "@"
                        + dest_machine.effective_host
                        + ":"
                        + str(dest_path),
                    ]
                )
                # wait - took some time after rsync that it appeared
                with dest_machine._shell() as shell:
                    for i in range(20):
                        if shell.exists(dest_path):
                            break
                        time.sleep(5)
                    else:
                        raise Exception((
                            "After rsync file was "
                            f"not found on {dest_machine.name}:{dest_path}"
                        ))
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
                    raise Exception("should exist")
            shell.rm("/tmp/hansi")
            for i in range(tests):
                value = shell.exists("/tmp/hansi")
                print(i, value)
                if value:
                    raise Exception("should exist")

    def _cron_update_dumps(self):
        for machine in self.search([]):
            self.env.cr.commit()
            machine.volume_ids.with_delay(
                identity_key=f"machine-update-vol-sizes-{machine.id}",
            )._update_sizes()
            self.env.cr.commit()

            self.env["cicd.dump"].with_delay(
                identity_key=f"dump-udpate-{machine.id}"
            )._update_dumps(machine)
            self.env.cr.commit()

    def _cron_update_docker_containers(self):
        machines = self.env["cicd.git.repo"].search([]).mapped("machine_id")
        self.env.cr.commit()

        for rec in machines:
            self.env.cr.commit()
            rec.with_delay(
                identity_key=f"docker-containers-{rec.id}-{rec.name}"
            )._fetch_psaux_docker_containers()

    def _fetch_psaux_docker_containers(self):
        self.ensure_one()
        with self._shell() as shell:
            tempfile_containers = self.tempfile_containers
            self.env.cr.commit()
            try:
                containers = shell.X(
                    ["docker", "ps", "-a", "--format", "{{ .Names }}\t{{ .State }}"],
                    timeout=20,
                )["stdout"].strip()
            except shell.TimeoutConnection:
                logger.warn("Timeout ssh.", exc_info=True)
                return

            containers_dict = {}
            for line in containers.split("\n")[1:]:
                try:
                    container, state = line.split("\t")
                except Exception:
                    # perhaps no access or so
                    continue
                containers_dict[container] = state
            path = Path(tempfile_containers)
            path.write_text(json.dumps(containers_dict))

    def _get_containers(self):
        with self._extra_env() as x_self:
            if not x_self.tempfile_containers:
                return {}
            path = Path(x_self.tempfile_containers)

        if not path.exists():
            self._fetch_psaux_docker_containers()
            self.env.cr.commit()

        if path.exists():
            try:
                containers = json.loads(path.read_text())
            except json.decoder.JSONDecodeError:
                containers = {}
            except Exception:
                raise
        else:
            containers = {}
        return containers

    def compute_tempfile_containers(self):
        for rec in self:
            # TODO configurable path sysparameter
            path = Path("/opt/out_dir/docker_states")
            path.mkdir(exist_ok=True, parents=True)
            self.tempfile_containers = (
                f"{path}/{self.env.cr.dbname}.machine." f"{rec.id}.containers"
            )

    def performance_ssh(self, lines=1000):
        self.ensure_one()
        branch = self.env["cicd.git.branch"].search([], limit=1)
        with branch.shell("test") as shell:
            output = shell.odoo("produce-test-lines", str(lines))
            assert len(output["stdout"].splitlines()) > 999

    @api.model
    def create(self, vals):
        machine = super().create(vals)
        machine._make_default_temp_dir()
        return machine

    def _make_default_temp_dir(self):
        for rec in self:
            if not rec.volume_ids.filtered(lambda x: x.ttype == "temp"):
                rec.volume_ids = [[0, 0, {"ttype": "temp", "name": "/tmp/cicd"}]]

from subprocess import check_output, check_call
import time
import tempfile
import json
import yaml
from pathlib import Path
import shutil
import os
import inspect
import os
from pathlib import Path
from robot.libraries.BuiltIn import BuiltIn

current_dir = Path(
    os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
)
MANIFEST_FILE = current_dir / "res" / "dirstruct" / "MANIFEST"
gimera_file = current_dir / "res" / "dirstruct" / "gimera.yml"
rsa_file = current_dir / "res" / "id_rsa"
rsa_file_pub = current_dir / "res" / "id_rsa.pub"


class cicd(object):
    def _get_MANIFEST(self, version):
        return eval(self.replace_vars(MANIFEST_FILE.read_text()))

    def assert_configuration(self):
        output = self.cicdodoo("config", "--full", output=True)
        assert (
            "ODOO_QUEUEJOBS_CRON_IN_ONE_CONTAINER=1" not in output
        ), "ODOO_QUEUEJOBS_CRON_IN_ONE_CONTAINER=1 not allowed"
        assert "RUN_ODOO_QUEUEJOBS: '1'" in output, "RUN_ODOO_QUEUEJOBS=1 required"
        assert "RUN_ODOO_CRONJOBS: '1'" in output, "RUN_ODOO_CRONJOBS=1 required"

        dumps_path = BuiltIn().get_variable_value("${DUMPS_PATH}")
        assert (
            f"DUMPS_PATH: {dumps_path}" in output
        ), f"Dumps path must point to {dumps_path}"

    def cicdodoo(self, *params, output=False):
        path = Path(BuiltIn().get_variable_value("${CICD_HOME}"))
        cmd = "./cicd " + " ".join(map(lambda x: f"'{x}'", filter(bool, params)))
        return self._sshcmd(cmd, cwd=path, output=output)

    def get_sshuser(self):
        sshuser = BuiltIn().get_variable_value("${ROBOTTEST_SSH_USER}")
        return sshuser

    def get_pubkey(self):
        return rsa_file_pub.read_text()

    def get_idrsa(self):
        return rsa_file.read_text()

    def _get_hostkey(self):
        path = Path("/tmp/key")
        if path.exists():
            check_call(["sudo", "rm", "-Rf", path])
        path.mkdir(exist_ok=True)
        shutil.copy(rsa_file, path / "id_rsa")
        shutil.copy(rsa_file_pub, path / "id_rsa.pub")
        check_call(["chmod", "500", path])
        check_call(["chmod", "400", path / "id_rsa"])
        check_call(["chmod", "400", path / "id_rsa.pub"])
        return path / "id_rsa"

    def _writefile(self, path, content):
        file = Path(tempfile.mktemp(suffix="."))
        file.write_text(content)
        rsa_file = self._get_hostkey()
        res = check_call(
            [
                "rsync",
                "-e",
                f"ssh -i {rsa_file} -o StrictHostKeyChecking=no",
                file,
                f"{self.get_sshuser()}@host.docker.internal:{path}",
            ]
        )
        file.unlink()

    def sshcmd(self, stringcommand, output=False, cwd=None):
        return self._sshcmd(stringcommand, output=output, cwd=cwd)

    def _transfer_tree(self, src, dest):
        cmd = [
            "/usr/bin/rsync",
            "-e",
            f"ssh -o StrictHostKeyChecking=no -i {self._get_hostkey()}",
            f"{src}/",
            f"{self.get_sshuser()}@host.docker.internal:{dest}/",
            "-arP",
        ]
        check_call(cmd)

    def _sshcmd(self, stringcommand, output=False, cwd=None):
        if cwd:
            stringcommand = f"cd '{cwd}' || exit -1;" f"{stringcommand}"
        cmd = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-i",
            self._get_hostkey(),
            f"{self.get_sshuser()}@host.docker.internal",
            f"{stringcommand}",
        ]
        if not output:
            res = check_call(cmd)
        else:
            res = check_output(cmd, encoding="utf8")
            return res

    def _prepare_git(self):
        check_call(["git", "config", "--global", "user.email", "testcicd@nowhere.com"])
        check_call(["git", "config", "--global", "user.name", "testcicd"])

    def make_odoo_repo(self, path, version):
        path = Path(path)

        if path.exists():
            shutil.rmtree(path)
        self._prepare_git()

        self._sshcmd(f"[ -e '{path}' ] && rm -Rf '{path}' || true")
        self._sshcmd(f"mkdir -p '{path}'")
        self._transfer_tree(current_dir / "res" / "dirstruct", path)
        self._writefile(
            path / "MANIFEST", json.dumps(self._get_MANIFEST(version), indent=4)
        )
        self._writefile(path / "gimera.yml", self.replace_vars(gimera_file.read_text()))
        cicd_home = BuiltIn().get_variable_value("${CICD_HOME}")
        self._sshcmd(f"rsync '{cicd_home}/odoo_admin/tests/addons_my' '{path}' -ar")
        self._sshcmd("git init .; git add .; git commit -am 'init'", cwd=path)
        self._sshcmd("~/.local/bin/gimera apply odoo", cwd=path)
        tmppath = path.parent / f"{path.name}.tmp"
        self._sshcmd(f"rm -Rf '{tmppath}'")
        self._sshcmd(f"mv '{path}' '{tmppath}'")
        self._sshcmd(f"git clone --bare 'file://{path}.tmp' '{path}'")
        self._sshcmd(f"rm -Rf '{path}.tmp'")

    def replace_vars(self, text):
        version = BuiltIn().get_variable_value("${ODOO_VERSION}")
        text = text.replace("__VERSION__", version)
        return text

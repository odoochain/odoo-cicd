#!/usr/bin/python3
import sys
import time
import os
import subprocess
from pathlib import Path
import click

CICD_USER = subprocess.check_output(["whoami"], shell=True, encoding="utf8").strip()
CICD_HOME = os.getcwd().strip()
SEP = 80 * "!"
subprocess.check_call("./cicd reload", shell=True)
config = subprocess.check_output("./cicd config --full", encoding="utf8", shell=True)

if not "DEVMODE: '1'" in config:
	click.secho(
		f"{SEP}\n" f"{SEP}\n" "DO NOT RUN ON PRODUCTION SYSTEM\n" f"{SEP}\n" f"{SEP}\n",
		fg='red',
	)
	sys.exit(1)

click.secho("Making sure that ssh access is possible", fg="yellow")
id_rsa = Path("odoo_admin/tests/res/id_rsa.pub").read_text().strip()
authorized = Path(os.path.expanduser("~/.ssh/authorized_keys"))
if id_rsa not in authorized.read_text():
    authorized.write_text(authorized.read_text() + "\n" + id_rsa)

subprocess.check_call("./cicd down -v", shell=True)
subprocess.check_call("./cicd up -d", shell=True)
subprocess.check_call("./cicd up -d postgres", shell=True)
time.sleep(5)
subprocess.check_call("./cicd -f db reset", shell=True)
subprocess.check_call("./cicd update", shell=True)
subprocess.check_call("./cicd dev-env set-password-all-users 1", shell=True)
subprocess.check_call("./cicd up -d", shell=True)
time.sleep(10)
import pudb;pudb.set_trace()
subprocess.check_call(
    f"./cicd robot tests_all "
	f"--param ROBOTTEST_SSH_USER={CICD_USER} "
	f"--param CICD_HOME={CICD_HOME} ",
    shell=True,
)

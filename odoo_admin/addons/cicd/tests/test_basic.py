import arrow
<<<<<<< HEAD
import shutil
=======
>>>>>>> 095c915c05ebada02c9717519bbde65a92a311e0
import os
import pprint
import logging
import time
import uuid
from datetime import datetime, timedelta
from unittest import skipIf
from odoo import api
from odoo import fields
from odoo.tests import common
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT
from odoo.exceptions import UserError, RedirectWarning, ValidationError, AccessError
<<<<<<< HEAD
import subprocess
from pathlib import Path

URL = "/opt/src" # use self
SSH_USER = 'unittest_cicd'
GIT_USER = 'odoofun'
GIT_PASSWORD = 'funtastic'
cache_repo = '/tmp/odoofun.git'
INTERNAL_REPO_PATH = '/opt/out_dir/odoofun.git'
REPO_PATH_ON_HOST = '/home/unittest_cicd/.odoo/'

=======

GIT_USER = 'odoofun'
GIT_PASSWORD = 'funtastic'
>>>>>>> 095c915c05ebada02c9717519bbde65a92a311e0

class TestBasicRepo(common.TransactionCase):

    def setUp(self):
        super().setUp()
<<<<<<< HEAD
        self._prepare_local_odoofun()

    def _prepare_local_odoofun(self):
        self.path_odoofun_root = Path(INTERNAL_REPO_PATH)
        try:
            out = subprocess.check_output(["git", "status"], cwd=self.path_odoofun_root).decode('utf-8')
            if len(out.split("\n")) > 1:
                raise Exception("Perhaps status displayed - should be clean.")
        except:
            if self.path_odoofun_root.exists():
                shutil.rmtree(self.path_odoofun_root)
            tmp1 = cache_repo
            subprocess.check_call(["rsync", "-ar", "--delete-after", URL + "/", tmp1])
            subprocess.check_call(["git", "init", "."], cwd=tmp1)
            subprocess.check_call(["git", "add", "."], cwd=tmp1)
            subprocess.check_call(["git", "config", "user.name", "unittest cicd"], cwd=tmp1)
            subprocess.check_call(["git", "config", "user.email", "unittest@cicd"], cwd=tmp1)
            subprocess.check_call(["git", "commit", "-am", "took_src"], cwd=tmp1)
            subprocess.check_call(["git", "clone", "--bare", tmp1, str(self.path_odoofun_root)])

=======
>>>>>>> 095c915c05ebada02c9717519bbde65a92a311e0

    def test_setuprepo(self):
        machine = self.env['cicd.machine'].create({
            'name': 'local',
            'ttype': 'dev',
<<<<<<< HEAD
            'host': '127.0.0.1',
            'is_docker_host': True,
            'ssh_key': self.PRIVATE_KEY,
            'ssh_pubkey': self.PUB_KEY,
        })
        
        dbserver = self.env['cicd.postgres'].create({
            'name': 'db server ' + str(arrow.get()),
            'db_host': os.environ['DB_HOST'],
            'db_user': os.environ['DB_USER'],
            'db_pwd': os.environ['DB_PWD'],
            'db_port': int(os.environ['DB_PORT']),
            'ttype': 'dev',
        })
        machine.postgres_server_id = dbserver

=======
        })
        machine.generate_ssh_key()
>>>>>>> 095c915c05ebada02c9717519bbde65a92a311e0
        repo = self.env['cicd.git.repo'].create({
            'name': 'odoofun-new',
            'machine_id': machine.id,
            'url': 'https://git.itewimmer.de/odoo/customs/odoofun',
            'username': GIT_USER,
            'password': GIT_PASSWORD
<<<<<<< HEAD
        })

    PUB_KEY = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDMiIcMW2u+J6qzF52209XLe5bIhhKcVzxltzhM9LDZWgM/mmkXClKZepD6cfxfVNmKM/OvgyDFi3e2s/fV0NZe/Y5lDl9VYZqHyjkWv3856bwbCoVQOkpyhPiNaxhFq9677E4aPwaSyB3g2IXcoAYdOn8GW6CiaErXbNh/YiiKUJKejCptJ7QNUfJWL9W/nqSTog/YQyWAUQQsIOvQnnbeTJNLiSW+gXZi7dDCT18MxsgYR4HA4va9vfgGY1IFuMe2XgFdltvxTx/MPoSZETsffffdteBc6XXlYcj9GbN5uSQUUzlvDKrKQSK8uFvRCkQe1eU1dj2G6eNsZi743e+HHg4eIon1s5mqMHqPOE0uwdGG9jmAD4L7RcWWI1s8RqdbuOulq7ZKCWxYQ4KoPUBVwakty9/BhPN9sbS6vEO5iE65YIIcjYCqOp15u1Ay0is8mXXmw+hyH3TvwLxrqlphJItdy+PB7ixiairpLkQxRW4Wv4QoQo+2ODrpgtVOBXk= root@odoodevelop1"
    PRIVATE_KEY = """-----BEGIN OPENSSH PRIVATE KEY-----
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
-----END OPENSSH PRIVATE KEY-----
    """
=======
        })
>>>>>>> 095c915c05ebada02c9717519bbde65a92a311e0

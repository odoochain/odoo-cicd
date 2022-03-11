import arrow
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
from odoo.tools import config
import threading


class TestBranchState(common.TransactionCase):

    def setUp(self):
        super().setUp()

    def test_state(self):
        machine = self.env['cicd.machine'].create({
            'name': 'machine1',
            'ttype': 'dev',
        })
        repo = self.env['cicd.git.repo'].create({
            'name': 'test',
            'machine_id': machine.id,
        })
        branch = self.env['cicd.git.branch'].create({
            'name': 'branch1',
            'repo_id': repo.id,
        })

        self.assertEqual(branch.state, 'new')

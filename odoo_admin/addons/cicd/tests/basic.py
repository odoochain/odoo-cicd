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

GIT_USER = 'odoofun'
GIT_PASSWORD = 'funtastic'

class TestBasicRepo(common.TransactionCase):

    def setUp(self):
        super().setUp()

    def test_setuprepo(self):
        repo = self.env['cicd.git.repo'].create({
            'name': 'odoofun-new'
            'url': 'https://git.itewimmer.de/odoo/customs/odoofun',
            'username': GIT_USER,
            'password': GIT_PASSWORD
        })
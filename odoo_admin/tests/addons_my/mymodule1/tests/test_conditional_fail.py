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
from pathlib import Path


class Test(common.TransactionCase):

    def setUp(self):
        super().setUp()

    def test_A(self):
        if Path("/opt/src/failtest").exists():
            raise Exception("Error")
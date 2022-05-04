import os
import inspect
from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError

from pathlib import Path
current_dir = Path(os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe()))))

class Queuejob(models.Model):
    _inherit = 'queue.job'

    @api.model
    def init(self):
        super().init()
        sql = (current_dir / 'trigger.sql').read_text()
        self.env.cr.execute(sql)


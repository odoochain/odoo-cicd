###########
# BETA TEST
##############
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class Base(models.AbstractModel):
    _inherit = 'base'

    def read(self, *args, **kwargs):
        self = self.with_context(prefetch_fields=False)
        return super().read(*args, **kwargs)

    def browse(self, *args, **kwargs):
        self = self.with_context(prefetch_fields=False)
        return super().browse(*args, **kwargs)
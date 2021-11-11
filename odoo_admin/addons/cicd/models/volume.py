from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from . import pg_advisory_lock
import threading
import logging
logger = logging.getLogger(__name__)


class CicdVolumes(models.Model):
    _inherit = ['cicd.mixin.size']
    _name = 'cicd.machine.volume'

    name = fields.Char("Path")
    machine_id = fields.Many2one('cicd.machine', string="Machine")
    ttype = fields.Selection([
        ('dumps', 'Dumps'),
        ('source', 'Source'),
        ('other', 'Other'),

    ], string="Type", required=True)
    used_size_human = fields.Char("Used Size", compute="_compute_numbers")
    free_size_human = fields.Char("Free Size", compute="_compute_numbers")
    total_size_human = fields.Char("Total Size", compute="_compute_numbers")
    used_size = fields.Integer("Used Size", compute="_compute_numbers")
    free_size = fields.Integer("Free Size", compute="_compute_numbers")
    total_size = fields.Integer("Total Size", compute="_compute_numbers")
    used_percent = fields.Float("Used %", compute="_compute_numbers")

    @api.model
    def _cron_update(self):
        self.sudo().search([])._update_sizes()

    def _update_sizes(self):
        for rec in self:
            with rec.machine_id._shell() as shell:
                try:
                    stdout = rec.machine_id._execute_shell([
                        "df", rec.name
                    ]).strip()
                except Exception as ex:
                    logger.error(ex)
                else:
                    while "  " in stdout:
                        stdout = stdout.replace("  ", " ")
                    stdout = stdout.split("\n")
                    if len(stdout) > 1:
                        stdout = stdout[-1]
                    stdout = stdout.split(" ")
                    rec.used_percent = used_percent = stdout[4].replace("%", "")
                    rec.total_size = int(stdout[1])
                    rec.used_size = int(stdout[2])
                    rec.free_size = int(stdout[3])
                    used = int(stdout[1])
                    stdout[1]
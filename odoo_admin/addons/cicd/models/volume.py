from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
import threading
import logging
import humanize
logger = logging.getLogger(__name__)


class CicdVolumes(models.Model):
    _inherit = ['cicd.mixin.size']
    _name = 'cicd.machine.volume'

    name = fields.Char("Path")
    machine_id = fields.Many2one('cicd.machine', string="Machine")
    ttype = fields.Selection([
        ('dumps', 'Dumps'),
        ('dumps_in', 'Dumps (just to import)'),
        ('source', 'Source'),
        ('other', 'Other'),

    ], string="Type", required=True)
    used_size_human = fields.Char("Used Size", compute="_compute_numbers")
    free_size_human = fields.Char("Free Size", compute="_compute_numbers")
    total_size_human = fields.Char("Total Size", compute="_compute_numbers")
    used_size = fields.Float("Used Size")
    free_size = fields.Float("Free Size")
    total_size = fields.Float("Total Size")
    used_percent = fields.Float("Used %", compute="_compute_numbers")

    @api.constrains("name")
    def _check_name(self):
        for rec in self:
            if '~' in (rec.name or ''):
                raise ValidationError("Dont use ~ use absolute paths.")
            if not rec.name.startswith("/"):
                raise ValidationError("Use absolute paths please.")

    @api.depends("used_size", "total_size", "free_size")
    def _compute_numbers(self):
        for rec in self:
            rec.used_size_human = humanize.naturalsize(rec.used_size * 1024 * 1024 * 1024)
            rec.free_size_human = humanize.naturalsize(rec.free_size * 1024 * 1024 * 1024)
            rec.total_size_human = humanize.naturalsize(rec.total_size * 1024 * 1024 * 1024)
            rec.used_percent = 100 * rec.used_size / rec.total_size if rec.total_size else 0

    @api.model
    def _cron_update(self):
        for volume in self.sudo().search([]):
            volume.with_delay(
                identity_key=(
                    "volumesize-"
                    f"{volume.id}"
                )
            )._update_sizes()

    def _update_sizes(self):
        for rec in self:
            with rec.machine_id._shell() as shell:
                try:
                    stdout = shell.X([
                        "df", rec.name
                    ])['stdout'].strip()
                except Exception as ex:
                    logger.error(ex)
                else:
                    while "  " in stdout:
                        stdout = stdout.replace("  ", " ")
                    stdout = stdout.split("\n")
                    if len(stdout) > 1:
                        stdout = stdout[-1]
                    stdout = stdout.split(" ")
                    rec.used_percent = stdout[4].replace("%", "")
                    rec.total_size = int(stdout[1]) / 1024 / 1024
                    rec.used_size = int(stdout[2]) / 1024 / 1024
                    rec.free_size = int(stdout[3]) / 1024 / 1024
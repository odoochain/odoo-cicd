from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class CicdExportExcel(models.Model):
    _name = 'cicd.export.excel'

    branch_id = fields.Many2one(
        'cicd.git.branch', string="Branch", required=True)
    filecontent = fields.Binary("Filecontent")
    filename = fields.Char("Filename", compute="_compute_filenanme")
    sql = fields.Text("SQL")

    def _compute_filename(self):
        for rec in self:
            rec.filename = (
                f"{self.id}"
                ".xlsx"
            )

    def _get_content(self):
        breakpoint()
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError

class CompressorOutput(models.Model):
    _name = 'cicd.compressor.output'

    compressor_id = fields.Many2one("cicd.compressor", required=True)
    output_filename = fields.Char("Filename", required=True)
    volume_id = fields.Many2one(
        'cicd.machine.volume', string="Volume", required=True)
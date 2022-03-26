from odoo import _, api, fields, models
import arrow
import logging
from odoo import SUPERUSER_ID
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF
from contextlib import contextmanager, closing
from odoo import registry
logger = logging.getLogger(__name__)

class Dump(models.Model):
    _inherit = ['cicd.mixin.size']
    _name = 'cicd.dump'
    _order = 'date_modified desc'

    active = fields.Boolean("Active", default=True)
    name = fields.Char("Name", required=True, readonly=True)
    machine_id = fields.Many2one("cicd.machine", string="Machine", required=True, readonly=True)
    date_modified = fields.Datetime("Date Modified", readonly=True)
    volume_id = fields.Many2one('cid.machine.volume', string="Volume", compute="_compute_volume")

    def _compute_volume(self):
        for rec in self:
            name = '/'.join(rec.name.split("/")[:-1])
            volumes = rec.machine_id.volume_ids.filtered(
                lambda x: x.name.startswith(name))
            rec.volume_id = False
            if volumes:
                rec.volume_id = volumes[0]

    def download(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': f'/download/dump/{self.id}',
            'target': 'new'
        }

    def unlink(self):
        if not self.env.context.get('dump_no_file_delete', False):
            for rec in self:
                volume = self.volume_id
                if volume and volume.ttype == 'dumps':
                    with self.machine_id._shell() as shell:
                        shell.rm(rec.name)

        return super().unlink()

    @api.model
    def _cron_update(self):
        for machine in self.env['cicd.machine'].sudo().search([]):
            self.with_delay(
                identity_key=(
                    "update-dump-"
                    f"{machine.id}"
                )
            )._update_dumps(machine)

    @api.constrains("name")
    def _check_name(self):
        for rec in self:
            while rec.name.endswith("/"):
                rec.name = rec.name[:-1]

    def _update_dumps(self, machine):
        breakpoint()
        env = self.env

        env['base'].flush()
        env.cr.commit()

        with machine._shell() as shell:
            try:
                for volume in machine.volume_ids.filtered(lambda x: x.ttype in ['dumps', 'dumps_in']):
                    env['base'].flush()
                    env.cr.commit()

                    splitter = "_____SPLIT_______"
                    volname = volume.name or ''
                    if not volname.endswith("/"):
                        volname += "/"
                    files = shell.X([
                        "find", volname,
                        "-maxdepth", "1",
                        "-printf", f"%f{splitter}%TY%Tm%Td %TH%TM%TS{splitter}%s\\n",
                    ])['stdout'].strip().split("\n")

                    Files = {}
                    for line in files:
                        filename, date, size = line.split(splitter)
                        if filename.endswith("/"):
                            continue
                        date = arrow.get(date[:15])
                        path = volname + filename
                        Files[path] = {
                            'date': date.strftime(DTF),
                            'size': int(size),
                        }
                        del path, date, filename, size, line

                    for filepath, file in Files.items():

                        dumps = self.sudo().with_context(active_test=False).search([
                            ('name', '=', filepath),
                            ('machine_id', '=', machine.id)
                            ])
                        if not dumps:
                            dumps = dumps.sudo().create({
                                'name': filepath,
                                'machine_id': machine.id,
                            })

                        dumps.ensure_one()
                        if not dumps.date_modified or dumps.date_modified.strftime(DTF) != file['date']:
                            dumps.date_modified = file['date']
                        if dumps.size != file['size']:
                            dumps.size = file['size']
                        env['base'].flush()
                        env.cr.commit()

                    for dump in dumps.search([('name', 'like', volname)]):
                        if dump.name.startswith(volname):
                            if dump.name not in Files:
                                dump.with_context(dump_no_file_delete=True).unlink()
                                env.cr.commit()
            except Exception:
                logger.error('error', exc_info=True)
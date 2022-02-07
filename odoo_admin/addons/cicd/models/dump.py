from odoo import _, api, fields, models
import arrow
import logging
logger = logging.getLogger(__name__)

class Dump(models.Model):
    _inherit = ['cicd.mixin.size']
    _name = 'cicd.dump'
    _order = 'date_modified desc'

    active = fields.Boolean("Active", default=True)
    name = fields.Char("Name", required=True, readonly=True)
    machine_id = fields.Many2one("cicd.machine", string="Machine", required=True, readonly=True)
    date_modified = fields.Datetime("Date Modified", readonly=True)

    def download(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            #'url': 'http://www.mut.de',
            'url': f'/download/dump/{self.id}',
            'target': 'new'
        }
        
    def unlink(self):
        for rec in self:
            with self.machine_id._shell() as shell:
                shell.rmifexists(rec.name)

        return super().unlink()

    @api.model
    def _cron_update(self):
        for machine in self.env['cicd.machine'].sudo().search([]):
            self._update_dumps(machine)

    @api.constrains("name")
    def _check_name(self):
        for rec in self:
            while rec.name.endswith("/"):
                rec.name = rec.name[:-1]

    def _update_dumps(self, machine):
        with machine._shell() as shell:
            for volume in machine.volume_ids.filtered(lambda x: x.ttype in ['dumps', 'dumps_in']):
                with machine._shell() as shell:
                    files = shell.X([
                        "ls", volume.name + "/"
                    ])['stdout'].strip().split("\n")

                    todo = self.env[self._name]
                    for file in files:
                        if not file:
                            continue
                        path = volume.name + "/" + file
                        dumps = self.sudo().with_context(active_test=False).search([('name', '=', path), ('machine_id', '=', machine.id)])
                        if not dumps:
                            dumps = dumps.sudo().create({
                                'name': path,
                                'machine_id': machine.id,
                            })
                            dumps._update_size()
                        else:
                            todo |= dumps

                    if todo:
                        todo.with_delay(
                            identity_key=f"get_dump_info",
                            eta=arrow.get().shift(minutes=60).strftime("%Y-%m-%d %H:%M:%S"),
                        )._update_size()

    def _update_size(self):
        for rec in self:
            machines = rec.mapped('machine_id')
            for machine in machines:
                dumps = rec.filtered(lambda x: x.machine_id == machine)
                with machine._shell() as shell:
                    for dump in dumps:
                        dump = dump.sudo()
                        if not shell.exists(dump.name):
                            dump.unlink()
                            continue

                        with machine._shell() as shell:
                            if shell.exists(dump.name):
                                dump.size = int(shell.X([
                                    'stat', '-c', '%s', dump.name
                                ])['stdout'].strip())
                                dump.date_modified = shell.X([
                                    'date', '-r', dump.name, '+%Y-%m-%d %H:%M:%S', '-u',
                                ])['stdout'].strip()

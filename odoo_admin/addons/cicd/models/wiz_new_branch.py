from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools.logsio_writer import LogsIOWriter

class NewBranch(models.TransientModel):
    _name = 'cicd.git.branch.new'

    repo_id = fields.Many2one('cicd.git.repo', string="Repo", required=True)
    source_branch_id = fields.Many2one('cicd.git.branch', string="Clone From", required=True)
    new_name = fields.Char("New Name", required=True)
    dump_id = fields.Many2one('cicd.dump', string="Dump")

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        if res.get('repo_id'):
            repo = self.env['cicd.git.repo'].browse(res['repo_id'])
            res['dump_id'] = repo.default_simulate_install_id_dump_id.id
        return res

    @api.onchange('repo_id')
    def _onchange_repo(self):
        self.dump_id = self.repo_id.default_simulate_install_id_dump_id

    @api.constrains("new_name")
    def _check_name(self):
        for rec in self:
            invalid_chars = '(_)/:?!#*\\ '
            for c in invalid_chars:
                if c in rec.new_name:
                    raise ValidationError(_("Invalid Name: " + rec.new_name))

    def ok(self):
        machine = self.repo_id.machine_id
        with LogsIOWriter.GET("cicd", "new_branch") as logsio:
            repo_path = self.repo_id._get_main_repo(tempfolder=True)
            with machine._gitshell(self.repo_id, cwd=repo_path, logsio=logsio) as shell:
                shell.checkout_branch(self.source_branch_id.name)
                if shell.branch_exists(self.new_name):
                    raise ValidationError(f"Branch {self.new_name} already exists.")
                shell.X(["git", "checkout", "-b", self.new_name])
                shell.X(["git", "remote", "set-url", 'origin', self.repo_id.url])
                shell.X(["git", "push", "--set-upstream", "-f", 'origin', self.new_name])
                branch = self.source_branch_id.create({
                    'name': self.new_name,
                    'dump_id': self.dump_id.id,
                    'backup_machine_id': self.repo_id.machine_id.id,
                })

            return {
                'view_type': 'form',
                'res_model': branch._name,
                'res_id': branch.id,
                'views': [(False, 'form')],
                'type': 'ir.actions.act_window',
                'target': 'current',
            }
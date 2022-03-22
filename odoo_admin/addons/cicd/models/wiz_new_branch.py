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
            repo = self.env['cicd.git.repo'].sudo().browse(res['repo_id'])
            res['dump_id'] = repo.default_simulate_install_id_dump_id.id
        return res

    @api.onchange('repo_id')
    def _onchange_repo(self):
        self.dump_id = self.repo_id.sudo().default_simulate_install_id_dump_id

    @api.constrains("new_name")
    def _check_name(self):
        for rec in self:
            invalid_chars = '()/:?!#*\\ '
            for c in invalid_chars:
                if c in rec.new_name:
                    raise ValidationError(_("Invalid Name: " + rec.new_name))

    def ok(self):
        branch = self.source_branch_id.create({
            'name': self.new_name,
            'dump_id': self.dump_id.id,
            'backup_machine_id': self.repo_id.sudo().machine.id,
            'force_prepare_dump': True,
        })
        self.with_delay()._make_branch(branch.sudo())
        return {'type': 'ir.actions.act_window_close'}

    def _make_branch(self, branch):
        machine = branch.repo_id.machine_id
        repo = branch.repo_id
        with LogsIOWriter.GET("cicd", "new_branch") as logsio:
            with repo._temp_repo(machine=machine) as repo_path:
                with machine._gitshell(
                    repo, cwd=repo_path, logsio=logsio
                ) as shell:

                    shell.checkout_branch(self.source_branch_id.name)
                    if shell.branch_exists(self.new_name):
                        raise ValidationError(
                            f"Branch {self.new_name} already exists.")

                    shell.X(["git", "checkout", "-b", self.new_name])
                    shell.X([
                        "git", "remote", "set-url", 'origin', repo.url])
                    shell.X([
                        "git", "push", "--set-upstream",
                        "-f", 'origin', self.new_name])
                    branch.fetch()

                return {
                    'view_type': 'form',
                    'res_model': branch._name,
                    'res_id': branch.id,
                    'views': [(False, 'form')],
                    'type': 'ir.actions.act_window',
                    'target': 'current',
                }
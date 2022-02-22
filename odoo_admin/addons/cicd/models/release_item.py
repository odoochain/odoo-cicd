import traceback
import psycopg2
import arrow
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from odoo.addons.queue_job.exception import RetryableJobError
from ..tools.logsio_writer import LogsIOWriter
import logging
logger = logging.getLogger(__name__)


class ReleaseItem(models.Model):
    _inherit = ['mail.thread']
    _name = 'cicd.release.item'
    _order = 'id desc'

    name = fields.Char("Version")
    release_id = fields.Many2one('cicd.release', string="Release")
    planned_date = fields.Datetime("Planned Deploy Date", default=lambda self: fields.Datetime.now(), tracking=True)
    done_date = fields.Datetime("Done", tracking=True)
    changed_lines = fields.Integer("Changed Lines", tracking=True)
    final_curtain = fields.Datetime("Final Curtains", tracking=True)
    log_release = fields.Text("Log")
    state = fields.Selection([
        ("new", "New"),
        ('done', 'Done'),
        ('failed', 'Failed'),
        ('ignore', 'Ignore'),
    ], string="State", default='new', required=True, tracking=True)
    computed_summary = fields.Text("Computed Summary", compute="_compute_summary", tracking=True)
    commit_ids = fields.Many2many('cicd.git.commit', string="Commits", help="Commits that are released.", tracking=True)
    branch_ids = fields.Many2many('cicd.git.branch', string="Branches", tracking=True)
    count_failed_queuejobs = fields.Integer("Failed Jobs", compute="_compute_failed_jobs")
    try_counter = fields.Integer("Try Counter", tracking=True)
    commit_id = fields.Many2one('cicd.git.commit', string="Released commit", help="After merging all tested commits this is the commit that holds all merged commits.")

    release_type = fields.Selection([
        ('standard', 'Standard'),
        ('hotfix', 'Hotfix'),
    ], default="standard", required=True, readonly=True)

    @api.constrains("state")
    def _ensure_one_item_only(self):
        for rec in self:
            if rec.state in ['new']:
                if rec.release_id.item_ids.filtered(lambda x: x.release_type == 'standard' and x.id != rec.id and x.state in ['new']):
                    breakpoint()
                    raise ValidationError(_("There may only be one new standard item!"))

    def open_window(self):
        self.ensure_one()
        return {
            'view_type': 'form',
            'res_model': self._name,
            'res_id': self.id,
            'views': [(False, 'form')],
            'type': 'ir.actions.act_window',
            'target': 'current',
        }

    def _on_done(self):
        # if not self.changed_lines:
        #     msg = "Nothing new to deploy"
        self.done_date = fields.Datetime.now()
        self.release_id.message_post_with_template(
            self.env.ref('cicd.mail_release_done').id,
            )
        self.state = 'done'
        self.branch_ids._compute_state()

    def _compute_failed_jobs(self):
        for rec in self:
            jobs = self.env['queue.job'].search([
                ('identity_key', 'ilike', f'release-item {rec.id}')
            ])
            rec.count_failed_queuejobs = len(jobs.filtered(lambda x: x.state == 'failed'))

    @api.model
    def create(self, vals):
        release = self.env['cicd.release'].browse(vals['release_id'])
        vals['name'] = release.sequence_id.next_by_id()
        res = super().create(vals)
        return res

    def _compute_summary(self):
        for rec in self:
            summary = []
            for branch in rec.branch_ids.sorted(lambda x: x.date):
                summary.append(f"* {branch.enduser_summary}")
            rec.computed_summary = '\n'.join(summary)

    def _trigger_do_release(self):
        for rec in self:
            rec.with_delay(
                identity_key=f"release-item {rec.id}",
            )._do_release()

    def perform_release(self):
        self._do_release()

    def _do_release(self):
        breakpoint()
        try:
            self.env.cr.execute("select id from cicd_release where id=%s for update nowait", (self.release_id.id,))
        except psycopg2.errors.LockNotAvailable as ex:
            raise RetryableJobError(
                f"Could not work exclusivley on release {self.release_id.id} - retrying in few seconds",
                ignore_retry=True, seconds=15) from ex
        if not self.release_id.active:
            return

        try:
            if self.state not in ['new']:
                return
            if self.release_type == 'hotfix' and not self.branch_ids:
                raise ValidationError("Hotfix requires explicit branches.")
            if not self.commit_id:  # needs a collected commit with everything on it
                raise RetryableJobError(
                    "Missing commit",
                    ignore_retry=True, seconds=120)

            if self.commit_id.test_state != 'success':
                raise RetryableJobError(
                    f"Release is missing a valid test run of {self.commit_id.name}",
                    ignore_retry=True, seconds=120)

            with self.release_id._get_logsio() as logsio:

                self.try_counter += 1
                release = self.release_id
                repo = self.release_id.repo_id.with_context(active_test=False)
                candidate_branch = repo.branch_ids.filtered(lambda x: x.name == self.release_id.candidate_branch)
                candidate_branch.ensure_one()
                if not candidate_branch.active:
                    raise UserError(f"Candidate branch '{self.release_id.candidate_branch}' is not active!")
                changed_lines = repo._merge(
                    candidate_branch,
                    release.branch_id,
                    set_tags=[f'{repo.release_tag_prefix}{self.name}-' + fields.Datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
                    logsio=logsio,
                )
                self.changed_lines += changed_lines

                try:
                    if not self.changed_lines:
                        self._on_done()
                        return

                    errors = self.release_id._technically_do_release(self)
                    if errors:
                        raise Exception(errors)
                    self._on_done()

                except Exception:
                    msg = traceback.format_exc()
                    self.release_id.message_post(body=f"Deployment of version {self.name} failed: {msg}")
                    self.state = 'failed'
                    logger.error(msg)
                    self.env.cr.commit()

                self.log_release = logsio.get_lines()
                self.env.cr.commit()

        except Exception:
            self.state = 'failed'
            msg = traceback.format_exc()
            self.release_id.message_post(body=f"Deployment of version {self.name} failed: {msg}")
            raise
        finally:
            self.env.cr.commit()

    def _get_ignored_branch_names(self, repo):
        all_releases = self.env['cicd.release'].sudo().search([
            ('branch_id.repo_id', '=', repo.id)
            ])
        ignored_branch_names = []
        ignored_branch_names += list(all_releases.mapped('candidate_branch'))
        ignored_branch_names += list(all_releases.mapped('branch_id.name'))
        return ignored_branch_names

    def _collect_tested_branches(self, repo):
        breakpoint()
        for rec in self:
            if rec.state not in ['new', 'failed']:
                continue
            if rec.release_type != 'standard':
                continue
            repo = rec.release_id.branch_id.repo_id

            ignored_branch_names = self._get_ignored_branch_names(repo)

            # select from many states:
            # * case: previous release may be failed: technical error, merge conflict
            # * case: state is done but was released at another release, so check again
            breakpoint()

            branches = self.env['cicd.git.branch'].search([
                ('state', 'in', ['candidate', 'tested', 'release', 'done']), # why so many states
                ('active', '=', True),
                ('block_release', '=', False),
                ('repo_id', '=', repo.id),
                ('name', 'not in', ignored_branch_names),
                ('id', 'not in', (rec.release_id.branch_id).ids),
            ]) | rec.branch_ids

            # remove branches, that are already merged
            branches = rec._filter_out_invalid_branches(branches)
            for branch in list(branches):
                if branch.latest_commit_id in rec.release_id.branch_id.commit_ids:
                    branches -= branch
            rec.branch_ids = [[6, 0, branches.ids]]

            rec._trigger_recreate_candidate_branch_in_git()

    def _filter_out_invalid_branches(self, branches):
        self.ensure_one()
        repo = self.release_id.repo_id
        ignored_branch_names = self._get_ignored_branch_names(repo)
        for b in list(branches):
            if b.state not in ['tested', 'candidate', 'done', 'release']:
                branches -= b
            if b.name in ignored_branch_names:
                branches -= b

        branches -= branches.filtered(lambda x: x.block_release or not x.active)
        return branches

    def _recreate_candidate_branch_in_git(self):
        """
        Heavy function - takes longer and does quite some work.
        """
        breakpoint()
        self.ensure_one()
        if self.state not in ('new', 'failed', 'ignore'):
            raise ValidationError("Branches can only be changed in state 'new', 'failed' or 'ignore'")

        # fetch latest commits:
        with self.release_id._get_logsio() as logsio:
            repo = self.release_id.repo_id.with_context(active_test=False)
            # remove blocked
            self.branch_ids = [[6, 0, self._filter_out_invalid_branches(self.branch_ids).ids]]
            critical_date = self.final_curtain or arrow.get().datetime
            commits = self._get_commits_within_final_curtains(critical_date)

            if set(commits.ids) == set(self.commit_ids.ids):
                return

            breakpoint()
            # if previous release has same commits like this one, then reuse 
            # the already merged branch here
            if len(self.release_id.item_ids) > 1:
                for prev_item in self.release_id.item_ids[1:]:
                    prev_item = self.release_id.item_ids[1]
                    prev_commits = prev_item.mapped('commit_ids')
                    if set(prev_commits.ids) == set(self.commit_ids.ids):
                        if not self.commit_id and prev_item.commit_id:
                            self.commit_id = prev_item.commit_id
                            return

            try:
                message_commit = repo._recreate_branch_from_commits(
                    commits=commits,
                    target_branch_name=self.release_id.candidate_branch,
                    logsio=logsio,
                    make_info_commit_msg=
                        f"Release Item {self.id}\n"
                        f"Includes latest commits from:\n{', '.join(self.mapped('branch_ids.name'))}"
                )
            except Exception as ex:
                msg = traceback.format_exc()
                self.state = 'failed'
                self.release_id.message_post(body=f"Merging into candidate failed {self.name} failed: {msg}")
                self.env.cr.commit()
                if logsio:
                    logsio.error(ex)
                logger.error(ex)
            else:
                if message_commit and commits:
                    message_commit.approval_state = 'approved'
                    self.commit_ids = [[6, 0, commits.ids]]
                    self.commit_id = message_commit
                    candidate_branch = repo.branch_ids.filtered(lambda x: x.name == self.release_id.candidate_branch)
                    candidate_branch.ensure_one()

                    (self.release_id.branch_id | self.branch_ids | candidate_branch)._compute_state()

    def _get_commits_within_final_curtains(self, critical_date):
        commits = self.env['cicd.git.commit']

        for branch in self.branch_ids:
            for commit in branch.commit_ids.sorted(lambda x: x.date, reverse=True):
                if critical_date:
                    if commit.date.strftime("%Y-%m-%d %H:%M:%S") > critical_date.strftime("%Y-%m-%d %H:%M:%S"):
                        continue

                if not commit.force_approved and (commit.test_state != 'success' or commit.approval_state != 'approved'):
                    continue

                commits |= commit

                break
        return commits

    def _trigger_recreate_candidate_branch_in_git(self):
        self.ensure_one()
        self.with_delay(
            identity_key=f"recreate_candidate_branch_in_git: {self.release_id.name}",
            eta=arrow.get().shift(minutes=1).datetime.strftime("%Y-%m-%d %H:%M:%S"),
        )._recreate_candidate_branch_in_git()

    @api.fieldchange("branch_ids")
    def _on_change_branches(self, changeset):
        for rec in self:
            rec._trigger_recreate_candidate_branch_in_git()
            (changeset['branch_ids']['old'] | changeset['branch_ids']['new'])._compute_state()

    def set_to_ignore(self):
        for rec in self:
            if rec.state not in ['failed', 'new']:
                raise ValidationError("Cannot set state to ignore")
            rec.state = 'ignore'

    def reschedule(self):
        for rec in self:
            if rec.state not in ['ignore']:
                raise ValidationError("Cannot set state to new")
            rec.state = 'new'

    def retry(self):
        for rec in self:
            if rec.state == 'failed':
                rec.state = 'new'
from odoo.tests import common
from odoo import fields
from datetime import datetime, timedelta


class TestSchedule(common.TransactionCase):

    def test_schedule(self):
        machine = self.env['cicd.machine'].create({
            'name': 'test',
            'ttype': 'dev',
        })
        repo = self.env['cicd.git.repo'].create({
            'name': 'test',
            'machine_id': machine.id,
        })
        branch = self.env['cicd.git.branch'].create({
            'name': 'test',
            'repo_id': repo.id,
        })
        release = self.env['cicd.release'].create({
            'name': 'test',
            'project_name': 'test',
            'repo_id': repo.id,
            'branch_id': branch.id,
            'sequence_id': self.env['ir.sequence'].create({
                'name': 'test',
            }).id,
        })

        release.schedule_line_ids = [(0, 0, {
            "name": "Every day 20 o'clock",
            "schedule": "0 20 * * *",
        })]

        now = datetime(2022, 3, 28, 11, 0, 0)
        next_date = release.schedule_line_ids._compute_next(now)
        expected = datetime(now.year, now.month, now.day, 20, 0, 0)

        self.assertEqual(expected, next_date)
        
        release.schedule_line_ids = [(0, 0, {
            "name": "Every day 19 o'clock",
            "schedule": "0 19 * * *",
        })]

        next_date = release._compute_next_date(now)
        expected = datetime(now.year, now.month, now.day, 19, 0, 0)
        self.assertEqual(expected, next_date)


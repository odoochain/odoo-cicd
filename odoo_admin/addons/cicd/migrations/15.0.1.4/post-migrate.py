from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    cr.execute("drop table cicd_test_run_line")

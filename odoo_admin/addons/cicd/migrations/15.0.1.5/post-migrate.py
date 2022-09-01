from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    for table in [
        "cicd_test_run_line_unittest",
        "cicd_test_run_line_robotest",
        "cicd_test_run_line_migration",
    ]:
        cr.execute(f"update {table} set date_finished='1980-04-04 00:00:00'")

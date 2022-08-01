import tempfile
<<<<<<< HEAD
import time
=======
>>>>>>> 19e91f84b1ffe3d25f527f57971cca7f5cb132b7
import os
import arrow
import base64
from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
import tempfile
from pathlib import Path
from odoo import _, api, fields, models, SUPERUSER_ID
from io import BufferedReader, BytesIO
from odoo.tools import convert_xml_import, convert_csv_import

from odoo.exceptions import UserError, RedirectWarning, ValidationError

<<<<<<< HEAD

class DataLoader(models.AbstractModel):
    _name = "robot.data.loader"

    @api.model
    def get_latest_file_in_folder(
        self, parent_dir, glob, younger_than, wait_until_exists
    ):
=======
class DataLoader(models.AbstractModel):
    _name = 'robot.data.loader'

    @api.model
    def get_latest_file_in_folder(self, parent_dir, glob, younger_than, wait_until_exists):
>>>>>>> 19e91f84b1ffe3d25f527f57971cca7f5cb132b7
        younger_than = arrow.get(younger_than)
        started = arrow.get()
        while (arrow.get() - started).total_seconds() < 20:

<<<<<<< HEAD
            files = list(
                sorted(
                    Path(parent_dir).glob(glob or "**/*"),
                    key=lambda x: x.stat().st_mtime,
                )
            )
=======
            files = list(sorted(Path(parent_dir).glob(glob or "**/*"), key=lambda x: x.stat().st_mtime))
>>>>>>> 19e91f84b1ffe3d25f527f57971cca7f5cb132b7
            files = [x for x in files if arrow.get(x.stat().st_mtime) > younger_than]
            if files:
                file = files[-1]
                return {
                    "filename": file.name,
                    "filepath": str(file),
<<<<<<< HEAD
                    "content": base64.b64encode(file.read_bytes()).decode("ascii"),
                }
            if not wait_until_exists:
=======
                    "content": base64.b64encode(file.read_bytes()).decode('ascii')
                }
            if not wait_until_exists: 
>>>>>>> 19e91f84b1ffe3d25f527f57971cca7f5cb132b7
                break
        return {}

    @api.model
    def put_file(self, filecontent, dest_path):
        content = base64.b64decode(filecontent)
        dest_path = Path(dest_path)
        dest_path.parent.mkdir(exist_ok=True, parents=True)
        dest_path.write_bytes(content)
        return True

    @api.model
    def execute_sql(self, sql):
        if os.getenv("DEVMODE") != "1":
            raise Exception("Requires devmode")
        self.env.cr.execute(sql)
        return True

    @api.model
    def load_data(self, content, file_type, module_name, filename):
<<<<<<< HEAD
        """Does basically the same like what at update happens when installing a module and
=======
        """Does basically the same like what at update happens when installing a module and 
>>>>>>> 19e91f84b1ffe3d25f527f57971cca7f5cb132b7
        loads the xml and csv files.

        Args:
            content ([string]): filecontent
            file_type (string): csv or xml
            module_name (string): faked module name
<<<<<<< HEAD
            filename (string):
=======
            filename (string): 
>>>>>>> 19e91f84b1ffe3d25f527f57971cca7f5cb132b7

        """

        filepath = Path(tempfile.mkstemp(suffix=file_type)[1])
        filepath.write_text(content)
        try:
<<<<<<< HEAD
            if file_type == ".xml":
                with open(filepath, "rb") as file:
=======
            if file_type == '.xml':
                with open(filepath, 'rb') as file:
>>>>>>> 19e91f84b1ffe3d25f527f57971cca7f5cb132b7
                    convert_xml_import(
                        self.env.cr,
                        module_name,
                        file,
                        idref={},
                        noupdate=False,
                    )
<<<<<<< HEAD
            elif file_type == ".csv":
=======
            elif file_type == '.csv':
>>>>>>> 19e91f84b1ffe3d25f527f57971cca7f5cb132b7
                convert_csv_import(
                    cr=self.env.cr,
                    module=module_name,
                    fname=Path(filename).name,
<<<<<<< HEAD
                    csvcontent=content.encode("utf-8"),
=======
                    csvcontent=content.encode('utf-8')
>>>>>>> 19e91f84b1ffe3d25f527f57971cca7f5cb132b7
                )
        finally:
            filepath.unlink()

<<<<<<< HEAD
        return True

    @api.model
    def wait_sqlcondition(self, sql):
        condition = None
        while condition or condition is None:
            self.env.cr.execute(sql)
            condition = self.env.cr.fetchall()[0][0]
            self.env.cr.commit()
            self.env.clear()
            time.sleep(0.5)
        return True

    @api.model
    def wait_queuejobs(self):
        self.wait_sqlcondition("select count(*) from queue_job where state not in ('done', 'failed');")
=======
>>>>>>> 19e91f84b1ffe3d25f527f57971cca7f5cb132b7
        return True
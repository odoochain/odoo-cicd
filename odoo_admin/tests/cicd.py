from subprocess import check_output, check_call
import json
import yaml
from pathlib import Path
import shutil
import os
import inspect
import os
from pathlib import Path
current_dir = Path(os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe()))))

class CICD(object):
    def _get_MANIFEST(self, version):
        return {
            "version": version,
        }

    def make_odoo_repo(self, path, version):
        path = Path(path)

        if path.exists():
            shutil.rmtree(path)

        path.mkdir(parents=True)
        os.chdir(path)
        (path / "MANIFEST").write_text(
            json.dumps(self._get_MANIFEST(version), indent=4)
        )
        (path / "gimera.yml").write_text(
            yaml.dump(
                {
                    "repos": [
                        {
                            "path": "odoo",
                            "type": "integrated",
                            "url": "https://github.com/odoo",
                            "branch": version,
                        }
                    ]
                }
            )
        )
        check_call(["git", "init", "."])
        check_call(["git", "commit", "-am", "init"])
        check_call(["gimera", "apply", "odoo"])

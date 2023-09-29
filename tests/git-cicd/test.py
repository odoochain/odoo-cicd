# _*_ coding:utf_8 _*_

"""
Used to demonstrate that it is possible to destroy a git folder by 
checking in/out in two threads.

"""
#!/usr/bin/env python3
import json
import sys
import time
import inspect
import os
import shutil
import tempfile
import subprocess
from pathlib import Path
import threading

current_dir = Path(
    os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
)

ROOT = Path(tempfile._get_default_tempdir()) / next(tempfile._get_candidate_names())
ROOT.mkdir()
print(f"Root is {ROOT}")
print("Please observe log.txt during the test")
time.sleep(5)

if sys.platform == 'win32':  # "Windows XP", "Windows 7", etc.
    GIT = current_dir.parent.parent / "bin/git-cicd"
elif sys.platform.startswith(('linux', 'freebsd', 'openbsd')):  # "Mac OS X", etc.
    GIT = "/usr/local/bin/git-cicd"

print(GIT)

flags = {}

errors = {
    "produce_noise": 0,
    "status": 0,
}
os.environ["GIT_TIMEOUT"] = "10"


def quick_checkouts(repo_path):
    subprocess.check_call([GIT, "checkout", "-b", "noisy1"], cwd=repo_path)
    subprocess.check_call([GIT, "checkout", "-b", "noisy2"], cwd=repo_path)
    subprocess.run(["rm", "-Rf", "odoo_admin"], cwd=repo_path)
    time.sleep(3)
    subprocess.check_output([GIT, "add", "."], cwd=repo_path)
    subprocess.check_output([GIT, "commit", "-am", "noise"], cwd=repo_path)
    flags["preparation_quick_checkouts"] = True
    while True:
        try:
            subprocess.check_output(
                [GIT, "checkout", "-f", "noisy1"],
                cwd=repo_path,
                env={"INITIAL_PWD": repo_path},
            )
            subprocess.check_output(
                [GIT, "checkout", "-f", "noisy2"],
                cwd=repo_path,
                env={"INITIAL_PWD": repo_path},
            )
        except Exception as ex:
            errors["produce_noise"] += 1
            errors["produce_noise_error"] = str(ex)


def git_status(repo_path):
    while True:
        try:
            subprocess.check_call(
                [GIT, "status"], cwd=repo_path, env={"INITIAL_PWD": repo_path}
            )
        except Exception as ex:
            errors["status"] += 1
            errors["status_last"] = str(ex)


def output():
    while True:
        Path("log.txt").write_text(json.dumps(errors, indent=4))


def test_conflict():
    repo_path = ROOT / "repo1"
    subprocess.check_call(
        [GIT, "clone", current_dir.parent.parent, repo_path], cwd=ROOT
    )

    threading.Thread(target=output).start()
    t = threading.Thread(target=quick_checkouts, args=(repo_path,))
    t.start()
    while not flags.get("preparation_quick_checkouts"):
        time.sleep(0.3)

    t1 = threading.Thread(target=git_status, args=(repo_path,))
    t1.start()

    for i in range(200):
        repo2 = ROOT / "repo2"
        print("Trying to clone")
        try:
            subprocess.check_call([GIT, "clone", repo_path, repo2], cwd=ROOT)
        except:
            print("Failed to clone")
            sys.exit(-1)
        shutil.rmtree(repo2)

    while True:
        time.sleep(1)


if __name__ == "__main__":
    test_conflict()
    if ROOT.exists():
        shutil.rmtree(ROOT)

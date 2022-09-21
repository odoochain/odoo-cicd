*** Settings ***
Documentation       Repo setup a repository

Resource            ../addons_robot/robot_utils/keywords/odoo_community.robot
Resource            ../addons_robot/robot_utils_common/keywords/tools.robot
Resource            keywords.robot
Library             OperatingSystem
Library             ./cicd.py

Suite Setup         Setup Suite
Test Setup          Setup Test


*** Test Cases ***

Test Run Release
    ${repo}=    Odoo Search    cicd.git.repo    domain=[]    limit=1
    ${postgres}=    Odoo Search    cicd.postgres    domain=[]    limit=1
    ${machine_id}=    Make Machine    ${postgres[0]}    source_dir=${DIR_RELEASED_VERSION}
    ${release}=    Make Release    repo_id=${repo[0]}    branch=main    machine_id=${machine_id}
    Odoo Execute    cicd.release    _cron_heartbeat
    Odoo Execute    cicd.release    _cron_heartbeat
    Odoo Execute    cicd.release    _cron_heartbeat

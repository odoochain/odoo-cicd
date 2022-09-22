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
    Setup Repository
    Fetch All Branches

    ${repo}=    Odoo Search    cicd.git.repo    domain=[]    limit=1
    ${postgres}=    Odoo Search    cicd.postgres    domain=[]    limit=1
    ${machine_id}=    Make Machine    ${postgres[0]}    source_dir=${DIR_RELEASED_VERSION}
    ${release}=    Make Release    repo_id=${repo[0]}    branch=main    machine_id=${machine_id}

    Log To Console    Make a new featurebranch
    ${commit_name}=    Set Variable    New Feature1
    cicd.Sshcmd    git clone ${SRC_REPO} ${CICD_WORKSPACE}/tempedit
    cicd.Sshcmd    git checkout -b feature1    cwd=${CICD_WORKSPACE}/tempedit
    cicd.Sshcmd    touch '${CICD_WORKSPACE}/tempedit/feature1'
    cicd.Sshcmd    git add feature1    cwd=${CICD_WORKSPACE}/tempedit
    cicd.Sshcmd    git commit -am '${commit_name}'    cwd=${CICD_WORKSPACE}/tempedit
    cicd.Sshcmd    git push --set-upstream origin feature1    cwd=${CICD_WORKSPACE}/tempedit

    Wait Until Commit Arrives    ${commit_name}
    ${branch_count}=    Odoo Search    cicd.git.branch    [('name', '=', 'feature1')]    count=True
    Should Be Equal As Strings    ${branch_count}    1
    Odoo Execute    cicd.release    cron_heartbeat
    Odoo Execute    cicd.release    cron_heartbeat
    Odoo Execute    cicd.release    cron_heartbeat

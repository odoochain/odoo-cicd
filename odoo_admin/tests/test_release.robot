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
    Log To Console  Configure postgres which runs as docker container
    ${postgres}=    Make Postgres  prodrelease  ttype=prod  db_host=postgres  db_port=5432
    ${machine_id}=    Make Machine    ${postgres[0]}    ssh_user=${ROBOTTEST_RELEASE_SSH_USER}  source_dir=${DIR_RELEASED_VERSION}    type=prod
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

    ${branch_id}=    Odoo Search    cicd.git.branch    [('name', '=', 'feature1')]
    Odoo Write    cicd.git.branch    ${branch_id}    ${{ {'enduser_summary': "summary1"} }}
    Odoo Execute    cicd.git.branch    method=set_approved    ids=${branch_id}
    Odoo Execute    cicd.git.branch    method=set_approved    ids=${branch_id}
    Repeat Keyword    5 times    Release Heartbeat

    ${release_item_id}=    Odoo Search    cicd.release.item    []    limit=1
    ${branches}=    Odoo Read Field    cicd.release.item    ${release_item_id}    branch_ids

    Should Be Equal As Strings    ${{str(len(${branches}))}}    1

    Odoo Execute    cicd.release.item    release_now    ${release_item_id}
    Repeat Keyword    5 times    Release Heartbeat

    Odoo Execute    cicd.git.branch    cron_run_open_tests
    Wait Queuejobs Done

    Repeat Keyword    5 times    Release Heartbeat

    ${state}=    Odoo Read Field    cicd.release.item    ${release_item_id}    state
    Should Be Equal As Strings    ${state}    done

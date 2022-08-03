*** Settings ***
Documentation    Repo setup a repository
Resource         ../addons_robot/robot_utils/keywords/odoo_community.robot
Resource         ../addons_robot/robot_utils_common/keywords/tools.robot
Library          OperatingSystem
Library          ./cicd.py

Suite Setup    Setup Suite
Test Setup     Setup Test

*** Variables ***

*** Test Cases ***
Setup Repository
    cicd.Make Odoo Repo    ${SRC_REPO}      ${ODOO_VERSION}
    ${postgres}=           Make Postgres
    ${machine}=            Make Machine     ${postgres}
    ${repo}=               Make Repo        ${machine}

Test Fetch All Branches
    ${repo}=                      Odoo Search        cicd.git.repo                 domain=[]                                       limit=1
    cicd.Cicdodoo                 up                 -d                            odoo_queuejobs
    Odoo Execute                  cicd.git.repo      method=fetch                  ids=${repo}
    Wait Queuejobs Done
    Odoo Execute                  cicd.git.repo      method=create_all_branches    ids=${repo}
    Wait Queuejobs Done
    ${main_count}=                Odoo Search        cicd.git.branch               domain=[['name', '=', 'main']]                  count=True
    Should Be Equal As Strings    ${main_count}      1
    ${main_branch}=               Odoo Search        cicd.git.branch               domain=[['name', '=', 'main']]
    Odoo Execute                  cicd.git.branch    method=update_git_commits     ids=${main_branch}
    Wait Queuejobs Done
    ${commits}=                   Odoo Search        cicd.git.commit               domain=[['branch_ids', '=', ${main_branch}]]    count=True
    Should Be Equal As Strings    ${commits}         3

Test Run Unittest
    ${main_branch}=               Odoo Search                   cicd.git.branch                       domain=[['name', '=', 'main']]
    Log To Console                Configuring a test setting
    ${values}=                    Create Dictionary             unittest_ids=${{[[0, 0, {}]]}}    robottest_ids=${{[[0, 0, {}]]}}
    Odoo Write                    cicd.git.branch               ids=${main_branch}                    values=${values}
    cicd.Cicdodoo                 up                            -d                                    odoo_queuejobs                                                                              odoo_cronjobs
    Odoo Execute                  cicd.git.branch               method=run_tests                      ids=${main_branch}
    ${testruns}=                  Odoo Search                   cicd.test.run                         domain=[['branch_id', '=', ${main_branch}]]                                                 count=True
    Should Be Equal As Strings    ${testruns}                   1
    Odoo Execute                  robot.data.loader             method=wait_sqlcondition              params=${{["select count(*) from cicd_test_run where state not in ('done', 'failed')"]}}

Test Run Release
    Log To Console    Todo



*** Keywords ***
Setup Test
    Login

Setup Suite
    ${CICD_DB_HOST}=            Get Environment Variable    CICD_DB_HOST
    ${CICD_DB_PORT}=            Get Environment Variable    CICD_DB_PORT
    Set Global Variable         ${CICD_HOME}                /home/cicd/cicd_app
    Set Global Variable         ${CICD_DB_HOST}
    Set Global Variable         ${CICD_DB_PORT}
    Set Global Variable         ${WORKSPACE}                /home/cicd/cicdtest_workspace
    Set Global Variable         ${SRC_REPO}                 ${WORKSPACE}/odoo1
    Set Global Variable         ${ROBOTTEST_REPO_URL}       file://${SRC_REPO}
    Set Global Variable         ${ODOO_VERSION}             15.0
    Set Global Variable         ${CICD_DB_HOST}             ${CICD_DB_HOST}
    Set Global Variable         ${CICD_DB_PORT}             ${CICD_DB_PORT}
    # user on host
    Set Global variable         ${ROBOTTEST_SSH_USER}       cicd
    ${ROBOTTEST_SSH_PUBKEY}=    cicd.Get Pubkey
    ${ROBOTTEST_SSH_KEY}=       cicd.Get IdRsa
    Set Global Variable         ${ROBOTTEST_SSH_PUBKEY}
    Set Global Variable         ${ROBOTTEST_SSH_KEY}
    Set Global Variable         ${DUMPS_PATH}               /tmp/cicd_test_dumps
    Set Global Variable         ${CICD_WORKSPACE}           /tmp/cicd_workspace

    cicd.Assert Configuration
    Log To Console               Kill Cronjobs and Queuejobs
    cicd.Cicdodoo                kill                           odoo_queuejobs    odoo_cronjobs
    cicd.Sshcmd                  rm -Rf ${CICD_WORKSPACE}
    cicd.Sshcmd                  mkdir -p ${CICD_WORKSPACE}
    cicd.Sshcmd                  mkdir -p ${DUMPS_PATH}
    cicd.Sshcmd                  rm -Rf ${CICD_WORKSPACE}/*

    Odoo Load Data    res/security.xml

Wait Queuejobs Done
    Odoo Execute    robot.data.loader    method=wait_queuejobs

Make Postgres
    ${uuid}=    Get Guid
    ${date}=    Get Now As String
    ${name}=    Set Variable         ${{$date + '-' + $uuid}}

    ${values}=                     Create Dictionary    name=${name}     ttype=dev       db_port=${CICD_DB_PORT}    db_host=${CICD_DB_HOST}
    ${postgres}=                   Odoo Create          cicd.postgres    ${values}
    Wait Until Keyword Succeeds    5x                   10 sec           Odoo Execute    cicd.postgres              method=update_databases    ids=${postgres}
    [return]                       ${postgres}

Make Machine
    [Arguments]    ${postgres}
    ${uuid}=       Get Guid
    ${date}=       Get Now As String
    ${name}=       Set Variable         ${{$date + '-' + $uuid}}


    ${values}=      Create Dictionary
    ...             name=${name}
    ...             is_docker_host=True
    ...             external_url=http://testsite
    ...             ttype=dev
    ...             ssh_user=${ROBOTTEST_SSH_USER}
    ...             ssh_pubkey=${ROBOTTEST_SSH_PUBKEY}
    ...             ssh_key=${ROBOTTEST_SSH_KEY}
    ...             postgres_server_id=${postgres}
    ${machine}=     Odoo Create                           cicd.machine                  ${values}
    Odoo Execute    cicd.machine                          method=test_ssh_connection    ids=${machine}

    ${values}=     Create Dictionary
    ...            ttype=source
    ...            name=${CICD_WORKSPACE}
    ...            machine_id=${machine}
    Odoo Create    cicd.machine.volume       ${values}

    ${values}=     Create Dictionary
    ...            ttype=dumps
    ...            name=${DUMPS_PATH}
    ...            machine_id=${machine}
    Odoo Create    cicd.machine.volume      ${values}
    [return]       ${machine}

Make Repo
    [Arguments]       ${machine}
    ${uuid}=          Get Guid
    ${date}=          Get Now As String
    Log To Console    Url to repository is ${ROBOTTEST_REPO_URL}

    ${values}=    Create Dictionary
    ...           name=${ROBOTTEST_REPO_URL}
    ...           default_branch=master
    ...           skip_paths=/release/
    ...           initialize_new_branches=True
    ...           release_tag_prefix=release-
    ...           login_type=nothing
    ...           machine_id=${machine}
    ${repo}=      Odoo Create                     cicd.git.repo    ${values}
    [return]      ${repo}
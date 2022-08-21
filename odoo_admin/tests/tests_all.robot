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
    ${machine}=            Make Machine     ${postgres}        source_dir=${CICD_WORKSPACE}
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
    ${main_branch}=               Odoo Search                   cicd.git.branch                   domain=[['name', '=', 'main']]
    Log To Console                Configuring a test setting
    ${values}=                    Create Dictionary             unittest_ids=${{[[0, 0, {}]]}}    robottest_ids=${{[[0, 0, {}]]}}
    Odoo Write                    cicd.git.branch               ids=${main_branch}                values=${values}
    cicd.Cicdodoo                 up                            -d                                odoo_queuejobs                                 odoo_cronjobs
    Remove File                   /opt/src/failtest
    Odoo Execute                  cicd.git.branch               method=run_tests                  ids=${main_branch}
    ${testruns}=                  Odoo Search                   cicd.test.run                     domain=[['branch_id', '=', ${main_branch}]]    count=True
    Should Be Equal As Strings    ${testruns}                   1
    Wait Testruns Done

    Log To Console     Now fail the test and make sure that test run is failed
    ${commit_name}=    Set Variable                                               failtest added
    cicd.Sshcmd        git clone ${SRC_REPO} ${CICD_WORKSPACE}/tempedit
    cicd.Sshcmd        touch '${CICD_WORKSPACE}/tempedit/failtest'
    cicd.Sshcmd        git add failtest; git commit -am '${commit_name}'          cwd=${CICD_WORKSPACE}/tempedit
    cicd.Sshcmd        git push

    Log To Console                 Wait till commit arrives
    Wait Until Keyword Succeeds    5x                          10 sec    Wait For Commit    ${commit_name}

    Append To File                /opt/src/failtest                 1
    Odoo Execute                  cicd.git.branch                   method=run_tests    ids=${main_branch}
    ${testruns}=                  Odoo Search                       cicd.test.run       domain=[['branch_id', '=', ${main_branch}]]    count=True
    Should Be Equal As Strings    ${testruns}                       2
    Wait Testruns Done
    ${testrun_id}=                Odoo Search                       cicd.test.run       domain=[['branch_id', '=', ${main_branch}]]    limit=1       order=id desc
    ${testrun_state} =            Odoo Read Field                   cicd.test.run       ${testrun_id}                                  state
    IF                            "${testrun_state}" != "failed"
    FAIL                          testrun should be failed
    END
    Remove File                   /opt/src/failtest

Test Run Release
    ${repo}=          Odoo Search     cicd.git.repo         domain=[]                             limit=1
    ${machine_id}=    Make Machine    ${postgres}           source_dir=${DIR_RELEASED_VERSION}
    ${release}=       Make Release    repo_id=${repo[0]}    branch=main                           ${machine_id[0]}
    Odoo Execute      cicd.release    _cron_heartbeat
    Odoo Execute      cicd.release    _cron_heartbeat
    Odoo Execute      cicd.release    _cron_heartbeat



*** Keywords ***
Setup Test
    Login

Setup Suite
    ${CICD_DB_HOST}=            Get Environment Variable    CICD_DB_HOST
    ${CICD_DB_PORT}=            Get Environment Variable    CICD_DB_PORT
    Set Global Variable         ${CICD_DB_HOST}
    Set Global Variable         ${CICD_DB_PORT}
    Set Global Variable         ${SRC_REPO}                 /tmp/odoo1
    Set Global Variable         ${ROBOTTEST_REPO_URL}       file://${SRC_REPO}
    Set Global Variable         ${ODOO_VERSION}             15.0
    Set Global Variable         ${CICD_DB_HOST}             ${CICD_DB_HOST}
    Set Global Variable         ${CICD_DB_PORT}             ${CICD_DB_PORT}
    # user on host
    ${ROBOTTEST_SSH_PUBKEY}=    cicd.Get Pubkey
    ${ROBOTTEST_SSH_KEY}=       cicd.Get IdRsa
    Set Global Variable         ${ROBOTTEST_SSH_PUBKEY}
    Set Global Variable         ${ROBOTTEST_SSH_KEY}
    Set Global Variable         ${DUMPS_PATH}               /tmp/cicd_test_dumps
    Set Global Variable         ${CICD_WORKSPACE}           /tmp/cicd_workspace
    Set Global Variable         ${DIR_RELEASED_VERSION}     /tmp/cicd_release1

    cicd.Assert Configuration
    Log To Console                  Kill Cronjobs and Queuejobs
    cicd.Cicdodoo                   kill                           odoo_queuejobs                   odoo_cronjobs
    Run keyword and ignore error    cicd.Sshcmd                    sudo rm -Rf ${CICD_WORKSPACE}
    cicd.Sshcmd                     rm -Rf ${CICD_WORKSPACE}
    cicd.Sshcmd                     mkdir -p ${CICD_WORKSPACE}
    cicd.Sshcmd                     mkdir -p ${DUMPS_PATH}
    cicd.Sshcmd                     rm -Rf ${CICD_WORKSPACE}/*

    Odoo Load Data    res/security.xml

Wait Testruns Done
    Odoo Execute    robot.data.loader    method=wait_sqlcondition    params=${{["select count(*) from cicd_test_run where state not in ('done', 'failed')"]}}

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
    [Arguments]    ${postgres}          ${source_dir}
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
    ...            name=${source_dir}
    ...            machine_id=${machine}
    Odoo Create    cicd.machine.volume      ${values}

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

Wait For Commit
    [Arguments]    ${commit_name}
    ${count}=      Odoo Search           model=cicd.git.commit    domain=[('text', 'like', '${commit_name}')]    count=True
    IF             "${count}"" == "0"
    FAIL           Commit not here
    END

Make Release
    [Arguments]            ${repo_id}                            ${branch}                               ${machine_id}
    ${sequence_id}         Odoo Create                           ir.sequence                             name=releaseseq            code=releaseseq
    ${branch_id}=          Odoo Search                           cicd.git.branch                         [['name', '=', branch]]
    ${action_ids}=         Set Variable                          ${{ [[0,0, {'machine_id': machine_id    }}
    ${common_settings}=    SEPARATOR=
    ...                    RUN_POSTGRES=1\\n
    ${values}=             Create Dictionary
    ...                    name=release
    ...                    repo_id=${repo_id}
    ...                    project_name=odoorelease
    ...                    branch_id=${branch_id[0]}
    ...                    auto_release=True
    ...                    action_ids=${action_ids}
    ...                    common_settings=${common_settings}
    ${release}=            Odoo Create                           cicd.release                            ${values}
    [return]               ${release}
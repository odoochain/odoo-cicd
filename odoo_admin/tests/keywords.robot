*** Settings ***
Resource    ../addons_robot/robot_utils/keywords/odoo_community.robot
Resource    ../addons_robot/robot_utils_common/keywords/tools.robot
Library     OperatingSystem
Library     ./cicd.py


*** Variables ***
${CICD_WORKSPACE}                   /tmp/cicd_workspace
${SRC_REPO}                         /tmp/odoo1
${ROBOTTEST_REPO_URL}               file://${SRC_REPO}
${ODOO_VERSION}                     15.0
${DUMPS_PATH}                       /tmp/cicd_test_dumps
${CICD_WORKSPACE}                   /tmp/cicd_workspace
${DIR_RELEASED_VERSION}             /tmp/cicd_release1
${DIR_TMP_RELEASE}                  /tmp/cicd_tmp_release
${ROBOTTEST_RELEASE_SSH_USER}       cicdrelease


*** Keywords ***
Setup Test
    Login
    Log To Console    Reducing wait time for finished queuejobs
    Odoo Sql    update ir_config_parameter set value = '2' where key='test.timeout.failed.queuejobs.minutes';

Setup Suite
    ${CICD_DB_HOST}=    Get Environment Variable    CICD_DB_HOST
    ${CICD_DB_PORT}=    Get Environment Variable    CICD_DB_PORT
    # TODO: whats this?
    Set Global Variable    ${CICD_DB_HOST}
    Set Global Variable    ${CICD_DB_PORT}
    Set Global Variable    ${CICD_DB_HOST}    ${CICD_DB_HOST}
    Set Global Variable    ${CICD_DB_PORT}    ${CICD_DB_PORT}

    # user on host
    ${ROBOTTEST_SSH_PUBKEY}=    cicd.Get Pubkey
    ${ROBOTTEST_SSH_KEY}=    cicd.Get IdRsa
    Set Global Variable    ${ROBOTTEST_SSH_PUBKEY}
    Set Global Variable    ${ROBOTTEST_SSH_KEY}

    cicd.Assert Configuration
    Log To Console    Kill Cronjobs and Queuejobs
    cicd.Cicdodoo    kill    odoo_queuejobs    odoo_cronjobs
    Run keyword and ignore error    cicd.Sshcmd    sudo rm -Rf ${CICD_WORKSPACE}
    cicd.Sshcmd    rm -Rf ${CICD_WORKSPACE}
    cicd.Sshcmd    mkdir -p "${CICD_WORKSPACE}"
    cicd.Sshcmd    chmod a+rw "${CICD_WORKSPACE}"
    cicd.Sshcmd    mkdir -p ${DUMPS_PATH}
    cicd.Sshcmd    mkdir -p ${DIR_TMP_RELEASE}
    cicd.Sshcmd    chmod a+rw "${DIR_TMP_RELEASE}"
    IF    "${CICD_WORKSPACE}" == ""    FAIL    requires CICD_WORKSPACE set
    cicd.Sshcmd    mkdir -p "${CICD_WORKSPACE}"
    cicd.Sshcmd    rm -Rf "${CICD_WORKSPACE}/*"
    cicd.Sshcmd    mkdir -p "${DIR_RELEASED_VERSION}"

    Odoo Load Data    res/security.xml

Wait Testruns Done
    Odoo Execute
    ...    robot.data.loader
    ...    method=wait_sqlcondition
    ...    params=${{["select count(*) from cicd_test_run where state not in ('done', 'failed')"]}}

Make Postgres
    [Arguments]    ${ttype}=dev    ${db_port}=${CICD_DB_PORT}    ${db_host}=${CICD_DB_HOST}
    ${uuid}=    Get Guid
    ${date}=    Get Now As String
    ${name}=    Set Variable    ${{$date + '-' + $uuid}}

    ${values}=    Create Dictionary
    ...    name=${name}
    ...    ttype=${ttype}    db_port=${db_port}    db_host=${db_host}
    ${postgres}=    Odoo Create    cicd.postgres    ${values}
    Wait Until Keyword Succeeds
    ...    5x
    ...    10 sec
    ...    Odoo Execute
    ...    cicd.postgres
    ...    method=update_databases
    ...    ids=${postgres}
    RETURN    ${postgres}

Make Machine
    [Arguments]    ${prefix}
    ...    ${postgres}    ${source_dir}    ${ttype}=dev
    ...    ${ssh_user}=${ROBOTTEST_SSH_USER}    ${tempdir}=${{ None }}

    ${uuid}=    Get Guid
    ${date}=    Get Now As String
    ${name}=    Set Variable    ${{$date + '-' + $uuid}}

    ${values}=    Create Dictionary
    ...    name=${prefix}${name}
    ...    is_docker_host=True
    ...    external_url=http://testsite
    ...    ttype=${ttype}
    ...    ssh_user=${ssh_user}
    ...    ssh_pubkey=${ROBOTTEST_SSH_PUBKEY}
    ...    ssh_key=${ROBOTTEST_SSH_KEY}
    ...    postgres_server_id=${postgres}
    ${machine}=    Odoo Create    cicd.machine    ${values}
    Odoo Execute    cicd.machine    method=test_ssh_connection    ids=${machine}

    ${values}=    Create Dictionary
    ...    ttype=source
    ...    name=${source_dir}
    ...    machine_id=${machine}
    Odoo Create    cicd.machine.volume    ${values}

    ${values}=    Create Dictionary
    ...    ttype=dumps
    ...    name=${DUMPS_PATH}
    ...    machine_id=${machine}
    Odoo Create    cicd.machine.volume    ${values}

    IF    ${tempdir}
        ${volume_id}=    Odoo Search    cicd.machine.volume
        ...    [('machine_id', '=', ${machine}), ('ttype', '=', 'temp')]

        Odoo Write    cicd.machine.volume    ${volume_id}    ${{ {'name': '${DIR_TMP_RELEASE}'} }}
    END

    RETURN    ${machine}

Make Repo
    [Arguments]    ${machine}
    ${uuid}=    Get Guid
    ${date}=    Get Now As String
    Log To Console    Url to repository is ${ROBOTTEST_REPO_URL}

    ${values}=    Create Dictionary
    ...    name=${ROBOTTEST_REPO_URL}
    ...    default_branch=main
    ...    skip_paths=/release/
    ...    initialize_new_branches=True
    ...    release_tag_prefix=release-
    ...    login_type=nothing
    ...    machine_id=${machine}
    ${repo}=    Odoo Create    cicd.git.repo    ${values}
    RETURN    ${repo}

Wait For Commit
    [Arguments]    ${commit_name}
    ${count}=    Odoo Search
    ...    model=cicd.git.commit
    ...    domain=[('text', 'like', '${commit_name}')]
    ...    count=True
    IF    "${count}" == "0"    FAIL    Commit not here

Make Release
    [Arguments]    ${repo_id}    ${branch}    ${machine_id}
    ${values}=    Create Dictionary    name=releaseseq
    ...    code=releaseseq
    ${sequence_id}=    Odoo Create    ir.sequence    ${values}

    ${branch_id}=    Odoo Search
    ...    cicd.git.branch
    ...    [['name', '=', '${branch}']]
    ${action_ids}=    Set Variable    ${{ [[0,0, {'machine_id': ${machine_id}}]] }}
    ${common_settings}=    Set Variable    RUN_POSTGRES=1\nRUN_ODOO_QUEUEJOBS=0\nRUN_ODOO_CRONJOBS=0

    ${values}=    Create Dictionary    name=release
    ...    project_name=odoorelease
    ...    branch_id=${branch_id[0]}
    ...    auto_release=True
    ...    repo_id=${repo_id}
    ...    sequence_id=${sequence_id}
    ...    common_settings=${common_settings}
    ...    action_ids=${action_ids}
    ${release}=    Odoo Create    cicd.release    ${values}
    RETURN    ${release}

Wait Until Commit Arrives    [Arguments]    ${commit_name}
    Log To Console    Wait till commit arrives
    ${repo}=    Odoo Search    cicd.git.repo    domain=[]    limit=1
    Log To Console    Fetching from Repo
    Odoo Execute    cicd.git.repo    method=fetch    ids=${repo}
    Log To Console    Waiting Queuejobs Done
    Wait Queuejobs Done
    Wait Until Keyword Succeeds    5x    10 sec    Wait For Commit    ${commit_name}

Fetch All Branches
    ${repo}=    Odoo Search
    ...    cicd.git.repo
    ...    domain=[]
    ...    limit=1
    cicd.Cicdodoo    up    -d    odoo_queuejobs
    Odoo Execute    cicd.git.repo    method=fetch    ids=${repo}
    Wait Queuejobs Done
    Odoo Execute    cicd.git.repo    method=create_all_branches    ids=${repo}
    Wait Queuejobs Done
    ${main_count}=    Odoo Search
    ...    cicd.git.branch
    ...    domain=[['name', '=', 'main']]
    ...    count=True
    Should Be Equal As Strings    ${main_count}    1
    ${main_branch}=    Odoo Search    cicd.git.branch    domain=[['name', '=', 'main']]
    Odoo Execute    cicd.git.branch    method=update_git_commits    ids=${main_branch}
    Wait Queuejobs Done
    ${commits}=    Odoo Search
    ...    cicd.git.commit
    ...    domain=[['branch_ids', '=', ${main_branch}]]
    ...    count=True
    Should Be Equal As Strings    ${commits}    4

Setup Repository
    cicd.Make Odoo Repo    ${SRC_REPO}    ${ODOO_VERSION}
    ${postgres}=    Make Postgres
    ${machine}=    Make Machine    dev    ${postgres}    source_dir=${CICD_WORKSPACE}
    ${repo}=    Make Repo    ${machine}

Release Heartbeat
    ${date}=    Get Now As String
    # Log To Console    FREE HAND for some hours
    # Sleep    10000s

    Log To Console    Release Heartbeat ${date}
    Wait Queuejobs Done
    Odoo Execute    cicd.release    cron_heartbeat
    Sleep    5s
    Wait Queuejobs Done
    Log To Console    Release Heartbeat Finished ${date}

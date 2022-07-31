*** Settings ***
Documentation     Repo setup a repository
Resource          ../addons_robot/robot_utils/keywords/odoo_community.robot
Resource          ../addons_robot/robot_utils_common/keywords/tools.robot
Library           OperatingSystem
Library           ./cicd.py

Test Setup        Setup Test


*** Test Cases ***
Setup Repository
    cicd.Make Odoo Repo              ${SRC_REPO}  ${ODOO_VERSION}
    ${postgres}=                     Make Postgres
    ${repo}=                         Make Repo  ${postgres}

*** Keywords ***
Setup Test
    ${CICD_DB_HOST}=                 Get Environment Variable    CICD_DB_HOST
    ${CICD_DB_PORT}=                 Get Environment Variable    CICD_DB_PORT
    Set Global Variable              ${CICD_DB_HOST}
    Set Global Variable              ${CICD_DB_PORT}
    Set Global Variable              ${WORKSPACE}  /home/cicd/cicdtest_workspace
    Set Global Variable              ${SRC_REPO}  ${WORKSPACE}/odoo1
    ${ROBOTTEST_REPO_URL}=           Convert To String      file://${SRC_REPO}
    Set Global Variable              ${ROBOTTEST_REPO_URL}
    Set Global Variable              ${ODOO_VERSION}  15.0
    Set Global Variable              ${CICD_DB_HOST}  ${CICD_DB_HOST}
    Set Global Variable              ${CICD_DB_PORT}  ${CICD_DB_PORT}
    # user on host
    Set Global variable              ${ROBOTTEST_SSH_USER}  cicd
    ${ROBOTTEST_SSH_PUBKEY}=         cicd.Get Pubkey
    ${ROBOTTEST_SSH_KEY}=            cicd.Get IdRsa

    # Login

Make Postgres
    ${uuid}=                         Get Guid
    ${date}=                         Get Now As String
    ${name}=                         Set Variable  ${{$date + '-' + $uuid}}

    ${values}=                       Create Dictionary  name=${name}  ttype=dev  db_port=${CICD_DB_PORT}  db_host=${CICD_DB_HOST}
    ${postgres}=                     Odoo Create   cicd.postgres  ${values}
                                     Odoo Execute  cicd.postgres  method=update_databases  ids=${postgres}
    [return]                         ${postgres}

Make Machine
    [Arguments]                      ${postgres}
    ${uuid}=                         Get Guid
    ${date}=                         Get Now As String
    ${name}=                         Set Variable ${{$date + '-' + $uuid}}


    ${values}=                       Create Dictionary
                                        ...   name=${name}
                                        ...   is_docker_host=True
                                        ...   external_url=http://testsite
                                        ...   ttype=dev
                                        ...   ssh_user=${ROBOTTEST_SSH_USER}
                                        ...   ssh_pubkey=${ROBOTTEST_SSH_PUBKEY}
                                        ...   ssh_key=${ROBOTTEST_SSH_KEY}
                                        ...   postgres_server_id=${postgres}
    ${machine}=                      Odoo Create   cicd.machine  ${values}
    [return]                         ${machine}

Make Repo
    [Arguments]                      ${machine}
    ${uuid}=                         Get Guid
    ${date}=                         Get Now As String
    ${name}=                         ${ROBOTTEST_REPO_URL}

    ${values}=                       Create Dictionary
                                     ...    name=${name}
                                     ...    default_branch=master
                                     ...    skip_paths=/release/
                                     ...    initialize_new_branches=True
                                     ...    release_tag_prefix=release-
    ${postgres}=                     Odoo Create   cicd.git.repo  ${values}
    [return]                         ${postgres}
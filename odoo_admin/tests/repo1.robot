*** Settings ***
Documentation     Repo setup a repository
Resource          keywords/odoo_15_cs.robot
Resource          keywords/tools.robot
Library           OperatingSystem

Test Setup        Setup Test


*** Test Cases ***
Setup Repository
    ${postgres}=                     Make Postgres
    ${repo}=                         Make Repo  ${postgres}

*** Keywords ***
Setup Test
    Login

Make Postgres
    ${uuid}=                         Get Guid
    ${date}=                         Get Now As String
    ${name}=                         Set Variable  ${{$date + '-' + $uuid}}
    ${CICD_DB_HOST}=                 Get Environment Variable    CICD_DB_HOST
    ${CICD_DB_PORT}=                 Get Environment Variable    CICD_DB_PORT

    ${values}=                       Create Dictionary  name=${name}  ttype=dev  db_port=${CICD_DB_PORT}  db_host=${CICD_DB_HOST}
    ${postgres}=                     Odoo Create   cicd.postgres  ${values}
                                     Odoo Execute  cicd.postgres  method=update_databases  ids=${postgres}
    [return]                         ${postgres}

Make Machine
    [Arguments]                      ${postgres}
    ${uuid}=                         Get Guid
    ${date}=                         Get Now As String
    ${name}=                         Set Variable ${{$date + '-' + $uuid}}

    ${ROBOTTEST_SSH_USER}            Get Environment Variable  ROBOTTEST_SSH_USER
    ${ROBOTTEST_SSH_PUBKEY}          Get Environment Variable  ROBOTTEST_SSH_PUBKEY
    ${ROBOTTEST_SSH_KEY}             Get Environment Variable  ROBOTTEST_SSH_KEY

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
    ${name}=                         ssh://git@git.itewimmer.de/odoo/customs/odoofun

    ${values}=                       Create Dictionary
                                     ...    name=${name}
                                     ...    default_branch=master
                                     ...    skip_paths=/release/
                                     ...    initialize_new_branches=True
                                     ...    release_tag_prefix=release-
    ${postgres}=                     Odoo Create   cicd.git.repo  ${values}
    [return]                         ${postgres}
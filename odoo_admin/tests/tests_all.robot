*** Settings ***
Documentation    Repo setup a repository
Resource         ../addons_robot/robot_utils/keywords/odoo_community.robot
Resource         ../addons_robot/robot_utils_common/keywords/tools.robot
Resource         keywords.robot
Library          OperatingSystem
Library          ./cicd.py

Suite Setup    Setup Suite
Test Setup     Setup Test

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
    Should Be Equal As Strings    ${commits}         4

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
    cicd.Sshcmd        git push                                                   cwd=${CICD_WORKSPACE}/tempedit

    Log To Console                 Wait till commit arrives
    ${repo}=                       Odoo Search                 cicd.git.repo    domain=[]          limit=1
    Log To Console                 Fetching from Repo
    Odoo Execute                   cicd.git.repo               method=fetch     ids=${repo}
    Log To Console                 Waiting Queuejobs Done
    Wait Queuejobs Done            
    Wait Until Keyword Succeeds    5x                          10 sec           Wait For Commit    ${commit_name}

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




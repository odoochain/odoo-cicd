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
    Setup Repository

Test Fetch All Branches
    Fetch All Branches

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

    Wait Until Commit Arrives    ${commit_name}

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




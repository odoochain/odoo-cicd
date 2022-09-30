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
Test Run Release 1
    [Documentation]    Normal release
    Test Run Release    deploy_git=${True}

Test Run Release 2
    [Documentation]    Not deploying git directory
    Test Run Release    deploy_git=${False}

Test Block Deployment
    ${release_item_id}=    Prepare Release
    Log To Console    The branch should be now already in the deployment; and now
    ...    it is decided to block it in last second; so check if
    ...    deployment is stopped
    ${branch_id}=    Odoo Search    cicd.git.branch    [('name', '=', 'feature1')]
    Odoo Write    cicd.git.branch    ${branch_id}    ${{ {'block_release': True } }}
    Release    release_item_id=${release_item_id}

Test Merge Conflict
    [Documentation]  Checks if albeit merge conflicts a release happens

    ${release_item_id}=  Prepare Release
    # Change the same file like in feature1 so that a conflict happens
    Make New Featurebranch
    ...    name=feature2
    ...    commit_name=New Feature2
    ...    filetotouch=feature1
    ...    filecontent=something_else

    Release  release_item_id=${release_item_id}
    ${branch_ids}=  Odoo Read Field  cicd.release.item  ${release_item_id}  branch_ids
    ${branches}=  Odoo Read  cicd.release.item.branch  ${branch_ids}  state
    Should Be True  'conflict' in [x['state'] for x in branches]

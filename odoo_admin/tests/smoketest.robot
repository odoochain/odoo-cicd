*** Settings ***
Documentation    Smoketest
Resource         ../addons_robot/robot_utils/keywords/odoo_community.robot
Test Setup       Setup Smoketest


*** Test Cases ***
Smoketest
    Search for the admin
    Capture Page Screenshot

*** Keywords ***
Setup Smoketest
    Login
    Odoo Load Data    res/security.xml

Search for the admin
    Odoo Search                   model=res.users    domain=[]          count=False
    ${count}=                     Odoo Search        model=res.users    domain=[('login', '=', 'admin')]    count=True
    Should Be Equal As Strings    ${count}           1
    Log To Console                ${count}


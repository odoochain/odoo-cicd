# Robot Utils Framework to use with wodoo

## Setup a new test

1. Add robot_utils and web_selenium to addons-paths
1. make a folder for your tests like /tests
1. make your first smoketest like 

```robot
*** Settings ***
Documentation     Smoketest
Resource          keywords/odoo_13_ee.robot
Test Setup        Setup Smoketest


*** Keywords ***
Setup Smoketest
    Login

Search for the admin
    Odoo Search                     model=res.users  domain=[]  count=False
    ${count}=  Odoo Search          model=res.users  domain=[('login', '=', 'admin')]  count=True
    Should Be Equal As Strings      ${count}  1
    Log To Console  ${count}


```
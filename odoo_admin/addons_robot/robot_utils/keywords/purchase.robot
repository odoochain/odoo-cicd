*** Settings ***
Resource          ../keywords/odoo_13_ee.robot

*** Variables ***

*** Keywords ***

Setup Purchase Demo Data
    ${PURCHASE_MODULE_NAME}=    Set Variable                 purchase_demodata
    Set Suite Variable          ${PURCHASE_MODULE_NAME}
    Odoo Load Data              data/partners_purchase.xml   ${PURCHASE_MODULE_NAME}

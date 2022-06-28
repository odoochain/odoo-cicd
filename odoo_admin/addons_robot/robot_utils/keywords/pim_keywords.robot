*** Settings ***
Resource          ../keywords/odoo_13_ee.robot

*** Variables ***

*** Keywords ***

Setup PIM Demo Data
    ${PIM_MODULE_NAME}=                  Set Variable    pim_demodata
    Set Suite Variable               ${PIM_MODULE_NAME}
    Odoo Load Data                  data/partners.xml                      ${PIM_MODULE_NAME}
    Odoo Load Data                  data/product_classification.xml        ${PIM_MODULE_NAME}
    Odoo Load Data                  data/products.xml                      ${PIM_MODULE_NAME}

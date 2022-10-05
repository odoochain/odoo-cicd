
*** Settings ***

Documentation    Roundcube Mail Client
Library          SeleniumLibrary
Library          ../library/tools.py

# Samples
#    Roundcube Send Mail    marc.wimmer@gmx.de    subject    body

*** Keywords ***

Open Roundcube
    Go To                               ${ODOO_URL}/mailer
    Wait Until Page Contains Element    jquery:ul#mailboxlist    timeout=10s
    Capture Page Screenshot

Roundcube Subject Should be visible
    [Arguments]                         ${subject}
    Go To                               ${ODOO_URL}/mailer
    Wait Until Page Contains Element    xpath://span[@class='subject']//span[contains(text(), '${subject}')]    timeout=4s

Roundcube Send Mail
    [Arguments]                      ${recipient}                        ${subject}       ${body}
    Open Roundcube
    Click Element                    jquery:a.compose
    Wait Until Element Is Visible    jquery:ul.recipient-input           timeout=3 sec
    Input Text                       jquery:ul.recipient-input input     ${recipient}
    Input Text                       jquery:input[name='_subject']       ${subject}
    Input Text                       jquery:textarea[name='_message']    ${body}
    Click Element                    jquery:button.send

Roundcube Clear All
    [Documentation]                  Erases all emails;
    Open Roundcube
    FOR                              ${i}                                         IN RANGE                     999999
    Run Keyword and Return Status    jquery:table#messagelist>tbody>tr:visible    timeout=5 sec
    ${present}=                      Run Keyword and Return Status                Element Should be Visible    jquery:table#messagelist>tbody>tr:visible
    Exit For Loop If                 not ${present}

    Run Keyword Unless                          ${i} > 1                          Click Element    jquery:table#messagelist>tbody>tr:visible
    Wait Until Page does NOT Contain Element    jquery:#messagestack div
    Wait Until Page Contains Element            jquery:a.delete:not(disabled)
    Click Element                               jquery:a.delete:not(disabled)
    Wait Until Page does NOT Contain Element    jquery:#messagestack div
    Log                                         Deleting a mail from roundcube
    Capture Page Screenshot
    END

*** Settings ***
Resource          ../resources/common.resource
Library           ../libraries/UserApi.py
Test Teardown     Clean Up Users

*** Variables ***
@{CREATED}    1    2

*** Test Cases ***
Create And Fetch User
    [Setup]    Authenticated Header For    admin
    ${user}=    Create Test User    alice
    ${fetched}=    Fetch User By Id    ${user}[id]
    Should Be Equal    ${fetched}[name]    alice
    User alice Should Exist

Delete User
    [Tags]    smoke
    ${user}=    Create Test User    bob
    UserApi.Delete User    ${user}[id]
    Reset Session

Auth Header Works
    ${h}=    Authenticated Header For    alice
    Run Keyword If    ${h}    Log    ok
    ...    ELSE    Create Test User    carol
    IF    ${h}
        Wait Until Keyword Succeeds    3x    1s    Fetch User By Id    1
    END

Templated Creation
    [Template]    Create Test User
    dave
    erin

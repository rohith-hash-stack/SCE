*** Settings ***
Library    ${CURDIR}/../libraries/UserApi.py    http://localhost:8000
Library    ../libraries/auth_helpers.py
Library    Collections

*** Keywords ***
Create Test User
    [Arguments]    ${name}
    ${user}=    Create User    ${name}    ${name}@example.com
    RETURN    ${user}

Authenticated Header For
    [Arguments]    ${username}
    ${token}=    Get Auth Token    ${username}    secret
    ${header}=    Build Auth Header    ${token}
    RETURN    ${header}

User ${name} Should Exist
    ${user}=    Fetch User By Id    ${name}
    Should Not Be Empty    ${user}

Clean Up Users
    FOR    ${id}    IN    @{CREATED}
        Delete User    ${id}
    END

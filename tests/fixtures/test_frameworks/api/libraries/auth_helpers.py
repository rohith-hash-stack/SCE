def get_auth_token(username, password):
    return f"token-{username}"


def build_auth_header(token):
    return {"Authorization": f"Bearer {token}"}

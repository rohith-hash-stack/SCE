import jwt as pyjwt


class TokenVerifier:
    def __init__(self, secret):
        self.secret = secret

    def verify(self, token):
        try:
            payload = pyjwt.decode(token, self.secret)
        except pyjwt.InvalidTokenError:
            raise PermissionError("invalid token")
        return payload


def verify_session(token):
    verifier = TokenVerifier("shared-secret")
    return verifier.verify(token)

import requests
from robot.api.deco import keyword, library


@library
class UserApi:
    """`@library` turns automatic keywords off: only `@keyword` methods are keywords."""

    def __init__(self, base_url="http://localhost"):
        self.base_url = base_url
        self.session = requests.Session()

    @keyword
    def create_user(self, name, email):
        response = self.session.post(f"{self.base_url}/users", json={"name": name, "email": email})
        return self._check(response)

    @keyword("Fetch User By Id")
    def get_user(self, user_id):
        return self._check(self.session.get(f"{self.base_url}/users/{user_id}"))

    @keyword(name="Delete User")
    def delete_user(self, user_id):
        return self._check(self.session.delete(f"{self.base_url}/users/{user_id}"))

    def reset_session(self):
        self.session = requests.Session()

    def _check(self, response):
        response.raise_for_status()
        return response.json()

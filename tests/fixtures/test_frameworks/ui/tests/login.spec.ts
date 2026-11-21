import { test, expect } from '../fixtures/base';
import { LoginPage } from '../pages/LoginPage';

test.describe('login', () => {
  test.beforeEach(async ({ loginPage }) => {
    await loginPage.goto();
  });

  test('valid user sees greeting', async ({ loginPage, dashboardPage }) => {
    await loginPage.login('alice', 'secret');
    expect(await dashboardPage.greeting()).toContain('alice');
  });

  test('explicit page object', async ({ page }) => {
    const login = new LoginPage(page);
    await login.goto();
    await login.login('bob', 'pw');
  });
});

test('logout returns to login', async ({ loginPage, dashboardPage }) => {
  await loginPage.login('alice', 'secret');
  await dashboardPage.logout();
});

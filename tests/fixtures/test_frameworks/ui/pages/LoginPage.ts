import { Page, Locator } from '@playwright/test';

export class LoginPage {
  readonly page: Page;
  readonly username: Locator;

  constructor(page: Page) {
    this.page = page;
    this.username = page.locator('#username');
  }

  async goto() {
    await this.page.goto('/login');
  }

  async login(user: string, password: string) {
    await this.username.fill(user);
    await this.page.fill('#password', password);
    await this.submit();
  }

  async submit() {
    await this.page.click('button[type=submit]');
  }
}

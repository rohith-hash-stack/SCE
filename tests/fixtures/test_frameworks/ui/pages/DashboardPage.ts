import { Page } from '@playwright/test';

export class DashboardPage {
  constructor(private readonly page: Page) {}

  async greeting(): Promise<string> {
    return (await this.page.textContent('.greeting')) ?? '';
  }

  async logout() {
    await this.page.click('#logout');
  }
}

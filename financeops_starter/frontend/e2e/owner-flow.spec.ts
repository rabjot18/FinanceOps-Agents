import { expect, test, type Page } from '@playwright/test';

// The Milestone 4 outcome on the Milestone 5 multi-page console, through a real
// browser and the real backend: load a synthetic case, propose a reminder,
// approve the exact message, queue it, let the worker deliver it to the
// SIMULATED inbox. Never clicks "Investigate with AI" (a paid model call).

async function signIn(page: Page) {
  await page.goto('/app/');
  await page.getByLabel('Owner token').fill('e2e-owner-token');
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('heading', { name: 'FinanceOps owner console' })).toBeVisible();
  await expect(page.getByText('Synthetic data · simulated delivery · no real money moves')).toBeVisible();
}

async function go(page: Page, link: string) {
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: new RegExp(link) }).click();
}

test('owner approves and queues a reminder that reaches the simulated inbox', async ({ page }) => {
  await signIn(page);

  await go(page, 'Demo Lab');
  await page.getByRole('button', { name: 'Load happy_path' }).click();
  await page.getByRole('link', { name: /Open case-sc-happy-inv-1/ }).click();
  await expect(page.getByRole('heading', { name: /sc-happy-inv-1/ }).first()).toBeVisible();

  await page.getByText('Propose an action manually').click();
  await page.getByRole('checkbox', { name: 'sc-happy-contract-1' }).check();
  await page.getByRole('button', { name: 'Propose', exact: true }).click();

  const action = page.locator('article.action-card').first();
  const message = action.getByRole('region', { name: 'Exact message' });
  await expect(message.getByText('accounts@maple-bakery.example')).toBeVisible();
  await expect(message.getByText('$5,000.00').first()).toBeVisible();

  // The sticky decision bar holds approve / execute.
  const bar = page.getByRole('region', { name: 'Decision' });
  await bar.getByRole('button', { name: 'Approve this exact message' }).click();
  await bar.getByRole('button', { name: /^Execute/ }).click();
  await expect(action.getByText(/^pending · attempts 0/)).toBeVisible();

  await go(page, 'Demo Lab');
  await page.getByRole('button', { name: 'Run worker once' }).click();
  await expect(page.getByRole('list', { name: 'Worker result' })).toContainText(/Sent to simulated inbox: act-/);

  await page.getByRole('button', { name: /simulated inbox/ }).click();
  const inbox = page.getByLabel('Simulated inbox messages');
  await expect(inbox).toContainText('accounts@maple-bakery.example');
  await expect(inbox).toContainText('sc-happy-inv-1');

  // Delivery changes no money: the case still shows the full amount outstanding.
  await go(page, 'Cases');
  const row = page.getByRole('row', { name: /Maple Bakery/ });
  await expect(row).toContainText('$5,000.00');
});

test('a document that tries to instruct the system is shown as plain text only', async ({ page }) => {
  const dialogs: string[] = [];
  page.on('dialog', (d) => {
    dialogs.push(d.message());
    void d.dismiss();
  });
  await signIn(page);

  await go(page, 'Demo Lab');
  await page.getByRole('button', { name: 'Load malicious_document' }).click();
  await page.getByRole('link', { name: /^Open case-/ }).first().click();
  await page.getByRole('tab', { name: /Evidence/ }).click();
  const evidence = page.locator('article.evidence').filter({ hasText: 'SYSTEM NOTICE TO THE AI ASSISTANT' });
  await expect(evidence).toBeVisible();
  await expect(evidence.locator('script, img, iframe')).toHaveCount(0);
  await expect(evidence.getByText('Untrusted').first()).toBeVisible();
  // The text changed nothing: no action exists on the case.
  await page.getByRole('tab', { name: /Messages/ }).click();
  await expect(page.locator('article.action-card')).toHaveCount(0);
  expect(dialogs).toEqual([]);
});

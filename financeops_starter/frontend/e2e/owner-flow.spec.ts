import { expect, test, type Page } from '@playwright/test';

// The Milestone 4 outcome, through a real browser and the real backend:
// load a synthetic case, propose a reminder, approve the exact message,
// queue it, let the worker deliver it to the SIMULATED inbox.

async function signIn(page: Page) {
  await page.goto('/app/');
  await page.getByLabel('Owner token').fill('e2e-owner-token');
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('heading', { name: 'FinanceOps owner console' })).toBeVisible();
  await expect(page.getByText('Synthetic data · simulated delivery · no real money moves')).toBeVisible();
}

test('owner approves and queues a reminder that reaches the simulated inbox', async ({ page }) => {
  await signIn(page);

  await page.getByRole('button', { name: 'Load happy_path' }).click();
  await expect(page.getByRole('heading', { name: /sc-happy-inv-1/ }).first()).toBeVisible();
  // Amounts are computed by the backend; the page only formats them.
  await expect(page.getByRole('button', { name: /Maple Bakery.*Outstanding \$5,000\.00/ })).toBeVisible();

  await page.getByText('Propose an action manually').click();
  await page.getByRole('checkbox', { name: 'sc-happy-contract-1' }).check();
  await page.getByRole('button', { name: 'Propose', exact: true }).click();

  const action = page.locator('article.action-card').first();
  const message = action.getByRole('region', { name: 'Exact message' });
  await expect(message.getByText('accounts@maple-bakery.example')).toBeVisible();
  await expect(message.getByText('$5,000.00').first()).toBeVisible();

  await action.getByRole('button', { name: 'Approve this exact message' }).click();
  await action.getByRole('button', { name: /^Execute/ }).click();
  await expect(action.getByText(/^pending · attempts 0/)).toBeVisible();

  await page.getByRole('button', { name: 'Run worker once' }).click();
  await expect(page.getByRole('list', { name: 'Worker result' })).toContainText(/Sent to simulated inbox: act-/);

  await page.getByRole('button', { name: /simulated inbox/ }).click();
  const inbox = page.getByLabel('Simulated inbox messages');
  await expect(inbox).toContainText('accounts@maple-bakery.example');
  await expect(inbox).toContainText('sc-happy-inv-1');

  // Delivery changes no money: the case still shows the full amount outstanding.
  await expect(page.getByRole('button', { name: /Maple Bakery.*Outstanding \$5,000\.00/ })).toBeVisible();
});

test('a document that tries to instruct the system is shown as plain text only', async ({ page }) => {
  const dialogs: string[] = [];
  page.on('dialog', (d) => {
    dialogs.push(d.message());
    void d.dismiss();
  });
  await signIn(page);

  await page.getByRole('button', { name: 'Load malicious_document' }).click();
  const evidence = page.locator('article.evidence').filter({ hasText: 'SYSTEM NOTICE TO THE AI ASSISTANT' });
  await expect(evidence).toBeVisible();
  await expect(evidence.locator('script, img, iframe')).toHaveCount(0);
  await expect(evidence.getByText('Untrusted').first()).toBeVisible();
  // The text changed nothing: no action exists on the case.
  await expect(page.locator('article.action-card')).toHaveCount(0);
  expect(dialogs).toEqual([]);
});

import { test, expect, Page } from "@playwright/test";
const A = "http://localhost:8000";
const draft = { draft: { cover_letter: "I built RAG pipelines with Celery.\n\nHappy to talk." }, claims: [{ claim: "I built RAG pipelines with Celery.", evidence_ids: ["ev1"] }],
  evidence: [{ id: "ev1", type: "case_study", source: "acme.md", outcome: "won" }], comments: [{ by: "critic", text: "Cut the first sentence" }], dry_run: { cost_connects: 12 } };

async function stub(page: Page, o: { healthy?: boolean } = {}) {
  const calls: any[] = [];
  await page.route(`${A}/health/marketplace`, r => r.fulfill({ json: { ok: o.healthy ?? true } }));
  await page.route(`${A}/auth/session`, r => r.fulfill({ json: { token: "t", user_id: "u", persona: JSON.parse(r.request().postData()!).persona } }));
  await page.route(`${A}/proposals/j1`, r => r.fulfill({ json: draft }));
  await page.route(`${A}/approvals/j1`, r => { calls.push(JSON.parse(r.request().postData()!)); const b = calls.at(-1);
    r.fulfill({ json: b.action === "edit" ? { status: "needs_fix", diff: "-old\n+new", pending_again: true } : { status: b.action === "approve" ? "submitted" : "rejected" } }); });
  return calls;
}
const studio = async (page: Page) => { await page.addInitScript(() => localStorage.setItem("token", "t")); await page.goto("/studio/j1"); };

test("1 onboarding and persona selection", async ({ page }) => {
  let sent: any; await stub(page);
  await page.route(`${A}/auth/session`, r => { sent = JSON.parse(r.request().postData()!); r.fulfill({ json: { token: "t", user_id: "u", persona: sent.persona } }); });
  await page.route(`${A}/jobs*`, r => r.fulfill({ json: [] }));
  await page.goto("/"); await page.getByTestId("email").fill("a@b.co");
  await page.getByTestId("persona-data_analyst").click(); await page.getByTestId("start").click();
  await expect(page).toHaveURL(/inbox/); expect(sent.persona).toBe("data_analyst");
});
test("2 approve resumes the graph and submits", async ({ page }) => {
  const calls = await stub(page); await studio(page);
  await expect(page.getByTestId("dry-run")).toBeVisible();
  await page.getByTestId("approve").click();
  await expect(page.getByTestId("status")).toContainText("submitted"); expect(calls).toEqual([{ action: "approve" }]);
});
test("3 reject with a reason", async ({ page }) => {
  const calls = await stub(page); await studio(page);
  await page.getByTestId("reason").fill("budget too low"); await page.getByTestId("reject").click();
  await expect(page.getByTestId("status")).toContainText("rejected"); expect(calls[0]).toEqual({ action: "reject", reason: "budget too low" });
});
test("4 edit captures a diff and requires re-approval", async ({ page }) => {
  const calls = await stub(page); await studio(page);
  await page.getByTestId("edit").click(); await page.getByTestId("edit-text").fill("Shorter letter."); await page.getByTestId("save-edit").click();
  await expect(page.getByTestId("diff")).toContainText("+new"); expect(calls[0]).toMatchObject({ action: "edit", text: "Shorter letter." });
  await expect(page.getByTestId("status")).toContainText("approve again");
});
test("5 degraded-mode banner when marketplace is down", async ({ page }) => {
  await stub(page, { healthy: false }); await page.goto("/");
  await expect(page.getByTestId("degraded-banner")).toContainText("Marketplace unavailable");
});

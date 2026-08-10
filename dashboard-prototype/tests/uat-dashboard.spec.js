import { expect, test } from "@playwright/test";

const dashboardCode = process.env.DASHBOARD_CODE;

async function login(page) {
  await page.goto("http://127.0.0.1:8765/");
  const codeInput = page.getByLabel("Mã đăng nhập");
  const overviewHeading = page.getByRole("heading", { name: "Tổng quan" });
  await expect(codeInput.or(overviewHeading)).toBeVisible();
  if (await codeInput.isVisible()) {
    if (!dashboardCode) throw new Error("DASHBOARD_CODE is required for dashboard UAT.");
    await codeInput.fill(dashboardCode);
    await page.getByRole("button", { name: /Đăng nhập owner/i }).click();
  }
  await expect(overviewHeading).toBeVisible();
}

async function openNavigationItem(page, label, mobile = false) {
  if (mobile) {
    await page.getByRole("button", { name: "Mở menu" }).click();
  }
  await page
    .getByRole("navigation", { name: "Điều hướng chính" })
    .getByRole("button", { name: label, exact: true })
    .click();
}

test.use({ channel: "chrome" });

test("owner flow uses live data and remains usable on desktop and mobile", async ({
  page,
}) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await login(page);
  consoleErrors.length = 0;
  await expect(page.locator("body")).not.toContainText("PROTOTYPE");
  await expect(page.locator("body")).not.toContainText("MOCK");

  const knowledgeResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/knowledge/sources") &&
      !response.url().includes("/export") &&
      response.ok(),
  );
  await openNavigationItem(page, "Kho tri thức");
  const knowledge = await (await knowledgeResponse).json();
  expect(knowledge.summary.total_sources).toBeGreaterThan(0);
  await expect(page.getByText("UNIFIED TELEGRAM BRAIN · LIVE")).toBeVisible();
  await expect(page.getByText(/nguồn đang đóng góp vào bộ não chung/)).toBeVisible();
  const selectPage = page.getByLabel("Chọn tất cả nguồn trên trang");
  await selectPage.check();
  await expect(page.getByRole("button", { name: /Chọn toàn bộ kết quả lọc/i })).toBeVisible();
  await selectPage.uncheck();
  await expect(page.getByLabel("Sắp xếp nguồn")).toBeVisible();

  await openNavigationItem(page, "Nhóm Telegram");
  const search = page.getByPlaceholder(/Tìm tên/i);
  await search.fill("@Group_68Trading");
  await expect(page.getByText("Coin68 Community | Chat")).toBeVisible();

  await openNavigationItem(page, "Bộ nhớ & lưu trữ");
  await expect(page.getByText("LOCAL STORAGE · LIVE")).toBeVisible();
  expect(await page.locator(".storage-list .ops-meter").count()).toBe(0);

  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);

  await page.setViewportSize({ width: 1024, height: 768 });
  await openNavigationItem(page, "Nhóm Telegram", true);
  await expect(page.getByText("Sắp xếp", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);

  await page.setViewportSize({ width: 390, height: 844 });
  await openNavigationItem(page, "Nhóm Telegram", true);
  await expect(page.getByRole("heading", { name: "Nhóm Telegram" })).toBeVisible();
  await expect(page.locator(".group-directory-table")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);

  await page.setViewportSize({ width: 360, height: 800 });
  await openNavigationItem(page, "Tài liệu & Hệ thống", true);
  await page.getByRole("button", { name: /USER_GUIDE\.md/i }).click();
  await expect(page.getByRole("dialog", { name: "USER_GUIDE.md" })).toBeVisible();
  await expect(page.getByPlaceholder("Tìm trong tài liệu…")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "USER_GUIDE.md" })).toBeHidden();
  expect(consoleErrors).toEqual([]);
});

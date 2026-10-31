import { expect, test } from "@playwright/test";

const fixtureUrl = (screen) => `/tests/dashboard-fixture.html?screen=${screen}`;

function group(index = 1, overrides = {}) {
  return {
    chat_id: String(-1000 - index), title: `Fixture ${String(index).padStart(2, "0")}`,
    username: `fixture_${index}`, chat_type: "channel", account_rights: {},
    last_seen_at: `2026-01-${String(index).padStart(2, "0")}T00:00:00Z`,
    activity: { last_message_at: "2025-01-01T00:00:00Z", observed_messages: 20 },
    policy: { allowed: true, ai_mode: "inherit", preferred_cloud_provider: "openai",
      cloud_fallback: false, ai_efficiency_preset: "balanced", filtering_level: "standard",
      rag_top_k: 5, rag_max_context_tokens: 2000, retention_days: 30,
      max_messages: 10000, max_storage_mb: 256, max_vectors: 10000 },
    permissions: { read_messages: true }, group_ai_ask: false, auto_link_moderation: false,
    knowledge: { status: "not_learned", mysql_message_count: index, vector_count: index * 2,
      storage_bytes: 0, last_job_id: null, last_error: null, last_learned_at: null },
    recommendation: { inactive_days: 100, reason: "Inactive fixture source" },
    ...overrides,
  };
}

async function mockApi(page, handler = () => undefined) {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const result = await handler(url, route.request());
    if (result !== undefined) return route.fulfill({ json: result });
    if (/\/auth\/(session|login)$/.test(url.pathname)) {
      return route.fulfill({ json: { owner_id: 1, csrf_token: "fixture-token" } });
    }
    if (url.pathname.endsWith("/events")) return route.abort();
    if (url.pathname.endsWith("/overview")) {
      return route.fulfill({ status: 503, json: { detail: "Fixture has no runtime" } });
    }
    if (url.pathname.endsWith("/preferences")) return route.fulfill({ json: {} });
    return route.fulfill({ json: { items: [], total: 0, pages: 0, page: 1 } });
  });
}

function source(index) {
  return { ...group(index), status: "not_learned", permissions: {},
    mysql_message_count: 0, vector_count: 0, storage_bytes: 0 };
}

test("failed filter refresh keeps the previous list visibly stale until Retry succeeds", async ({ page }) => {
  await mockApi(page);
  let fail = true;
  await page.route("**/knowledge/sources?**", async (route) => {
    const query = new URL(route.request().url()).searchParams.get("query");
    if (query && fail) return route.fulfill({ status: 503, json: { detail: "Refresh fixture unavailable" } });
    return route.fulfill({ json: { items: [source(query ? 2 : 1)], total: 1, pages: 1,
      page: 1, summary: { total_sources: 1 } } });
  });
  await page.goto(fixtureUrl("knowledge"));
  await expect(page.locator(".knowledge-source-table")).toContainText("Fixture 01");
  await page.locator(".search-field input").fill("new-filter");
  const warning = page.locator(".resource-notice").filter({ hasText: "Refresh fixture unavailable" });
  await expect(warning).toContainText("Dữ liệu cũ");
  await expect(warning).toContainText("Lần tải thành công");
  await expect(page.locator(".knowledge-source-table")).toContainText("Fixture 01");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(warning.getByRole("button", { name: "Thử lại" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath("knowledge-stale-mobile.png"), fullPage: true });
  fail = false;
  await warning.getByRole("button", { name: "Thử lại" }).click();
  await expect(page.locator(".knowledge-source-card")).toContainText("Fixture 02");
  await expect(page.locator(".resource-notice")).toHaveCount(0);
});

test("an older manual reload cannot replace data from a newer scope", async ({ page }) => {
  await mockApi(page);
  let calls = 0;
  let oldRoute;
  await page.route("**/groups/-100*", async (route) => {
    if (route.request().url().endsWith("-1001") && ++calls > 1) {
      oldRoute = route;
      return;
    }
    return route.fulfill({ json: { title: route.request().url().endsWith("-1002") ? "New scope" : "Initial scope" } });
  });
  await page.goto(fixtureUrl("resource"));
  await expect(page.getByTestId("resource-value")).toHaveText("Initial scope");
  await page.getByRole("button", { name: "Reload resource" }).click();
  await expect.poll(() => Boolean(oldRoute)).toBe(true);
  await page.getByRole("button", { name: "Other fixture group" }).click();
  await expect(page.getByTestId("resource-value")).toHaveText("New scope");
  await oldRoute.fulfill({ json: { title: "Obsolete reload" } });
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(page.getByTestId("resource-value")).not.toHaveText("Obsolete reload");
  await expect(page.getByTestId("resource-value")).toHaveText("New scope");
});

test("filtered learning selection reports its 500-source batch and prevents manual overflow", async ({ page }) => {
  const items = Array.from({ length: 501 }, (_, index) => source(index + 1));
  const submissions = [];
  await mockApi(page, (url, request) => {
    if (url.pathname.endsWith("/knowledge/sources")) {
      const size = Number(url.searchParams.get("page_size"));
      const currentPage = Number(url.searchParams.get("page"));
      return { items: items.slice((currentPage - 1) * size, currentPage * size),
        total: items.length, pages: Math.ceil(items.length / size), page: currentPage,
        summary: { total_sources: items.length } };
    }
    if (url.pathname.endsWith("/enable-selection-action")) {
      submissions.push(request.postDataJSON().chat_ids);
      return { action_id: "selection-preview" };
    }
  });
  await page.goto(fixtureUrl("knowledge"));
  await page.getByRole("checkbox", { name: "Chọn tất cả nguồn trên trang" }).check();
  await page.getByRole("button", { name: /Chọn.*500/ }).click();
  await expect(page.locator(".knowledge-bulk-toolbar")).toContainText("500/501");
  await expect(page.locator(".knowledge-bulk-toolbar")).toContainText("1 nguồn chưa chọn");
  await page.getByRole("button", { name: "Học nguồn đã chọn", exact: true }).click();
  await expect.poll(() => submissions.length).toBe(1);
  expect(submissions[0]).toHaveLength(500);
  expect(new Set(submissions[0]).size).toBe(500);
  // Go to the eleventh 50-row page, whose only row is outside the selected batch.
  for (let index = 0; index < 10; index++) {
    await page.locator(".pagination button").last().click();
    await expect(page.locator(".knowledge-source-table tbody tr").first()).toContainText(`Fixture ${String((index + 1) * 50 + 1).padStart(2, "0")}`);
  }
  await page.getByRole("checkbox", { name: "Chọn tất cả nguồn trên trang" }).click();
  await expect(page.getByRole("checkbox", { name: "Chọn tất cả nguồn trên trang" })).not.toBeChecked();
  await expect(page.getByTestId("fixture-toast")).toContainText("Tối đa 500");
  await expect(page.locator(".knowledge-bulk-toolbar")).toContainText("500 nguồn đã chọn");
  await page.getByRole("button", { name: "Học nguồn đã chọn", exact: true }).click();
  await expect.poll(() => submissions.length).toBe(2);
  expect(submissions[1]).toHaveLength(500);
});

for (const screen of ["overview", "connections"]) {
  test(`${screen} shows failed measured health alongside latency, and missing checks stay unknown`, async ({ page }) => {
    let health = [{ component: "mysql", status: "failed", latency_ms: 7,
      checked_at: new Date().toISOString() }];
    await mockApi(page, (url) => {
      if (url.pathname.endsWith("/overview")) return { health, joined_sources: 1 };
      if (url.pathname.endsWith("/workers")) return { latest_metric: { vector_bytes: 12 }, history: [] };
    });
    await page.goto(fixtureUrl(screen));
    const measured = screen === "overview" ? page.locator(".service-row").filter({ hasText: /mysql/i })
      : page.locator(".connection-card").filter({ has: page.getByRole("heading", { name: "MySQL", exact: true }) });
    await expect(measured).toContainText("FAILED");
    if (screen === "overview") await expect(measured).toContainText("7 ms");
    else await expect(measured.locator("input").first()).toHaveValue(/7 ms/);
    await expect(measured).toContainText("Kiểm tra");
    if (screen === "connections") {
      await expect(page.locator(".connection-card").filter({ hasText: "Qdrant" })).toContainText("UNKNOWN");
    }
    health = [{ component: "mysql", status: "ok", latency_ms: 7, checked_at: "2020-01-01T00:00:00Z" }];
    await page.getByRole("button", { name: "Refresh fixture" }).click();
    await expect(measured).toContainText("STALE");
    health = [];
    await page.getByRole("button", { name: "Refresh fixture" }).click();
    await expect(screen === "overview" ? page.locator(".service-list") : measured).toContainText("UNKNOWN");
  });
}

test("a measured OK check ages to STALE without another network response", async ({ page }) => {
  const checkedAt = new Date();
  await page.clock.install({ time: checkedAt });
  await mockApi(page, (url) => url.pathname.endsWith("/overview")
    ? { health: [{ component: "mysql", status: "ok", checked_at: checkedAt.toISOString() }] } : undefined);
  await page.goto(fixtureUrl("connections"));
  const card = page.locator(".connection-card").filter({ hasText: "MySQL" });
  await expect(card.locator(".badge")).toHaveText("OK");
  await page.clock.fastForward(6 * 60 * 1000);
  await expect(card.locator(".badge")).toHaveText("OK · STALE");
  await page.screenshot({ path: test.info().outputPath("connections-stale-desktop.png"), fullPage: true });
});

test("inactive recommendations honor filters, sorting, and ten-row pagination", async ({ page }) => {
  const items = Array.from({ length: 25 }, (_, index) => group(index + 1));
  items[0] = group(1, { chat_type: "supergroup", policy: { ...group().policy, allowed: false },
    knowledge: { ...group().knowledge, status: "learned" } });
  items[1] = group(2, { chat_type: "supergroup",
    knowledge: { ...group().knowledge, status: "learned" } });
  items[2] = group(3, { chat_type: "supergroup", policy: { ...group().policy, allowed: false } });
  await mockApi(page, (url) => url.pathname.endsWith("/recommendations")
    ? { items, total: items.length, unknown_activity: 0, threshold_days: 60 } : undefined);
  await page.goto(fixtureUrl("groups"));
  await page.getByRole("tab").nth(2).click();
  const rows = page.locator(".group-directory-table tbody tr");
  await expect(rows).toHaveCount(10);
  await page.locator(".pagination button").last().click();
  await expect(rows).toHaveCount(10);
  await expect(rows.first()).toContainText("Fixture 15");
  await page.locator(".pagination button").last().click();
  await expect(rows).toHaveCount(5);
  await page.locator(".advanced-filter-bar select").nth(5).selectOption("25");
  await expect(rows).toHaveCount(25);
  await page.locator(".advanced-filter-bar select").nth(3).selectOption("title");
  await page.locator(".advanced-filter-bar select").nth(4).selectOption("asc");
  await expect(rows.first()).toContainText("Fixture 01");
  await page.locator(".advanced-filter-bar select").nth(0).selectOption("supergroup");
  await expect(rows).toHaveCount(3);
  await page.locator(".advanced-filter-bar select").nth(1).selectOption("block");
  await expect(rows).toHaveCount(2);
  await page.locator(".advanced-filter-bar select").nth(2).selectOption("learned");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Fixture 01");
  await page.locator(".search-field input").fill("@fixture_1");
  await expect(rows).toHaveCount(1);
  await page.locator(".search-field input").fill("missing-source");
  await expect(rows).toHaveCount(0);
});

test("removing the last recommendation on a page returns to the preceding page", async ({ page }) => {
  let items = Array.from({ length: 11 }, (_, index) => group(index + 1));
  await mockApi(page, (url, request) => {
    if (url.pathname.endsWith("/recommendations")) return { items, total: items.length };
    if (url.pathname.endsWith("/preferences") && request.method() === "PUT") {
      const body = request.postDataJSON();
      items = items.filter((item) => !body.always_keep_chat_ids.includes(Number(item.chat_id)));
      return body;
    }
  });
  await page.goto(fixtureUrl("groups"));
  await page.getByRole("tab").nth(2).click();
  await expect(page.locator(".group-directory-table tbody tr")).toHaveCount(10);
  await page.locator(".pagination button").last().click();
  await expect(page.locator(".group-directory-table tbody tr")).toHaveCount(1);
  await page.locator(".group-directory-table tbody tr .row-actions button").first().click();
  await expect(page.locator(".group-directory-table tbody tr")).toHaveCount(10);
  await expect(page.locator(".pagination button").first()).toBeDisabled();
});

test("switching groups hides the previous group's controls until the requested group loads", async ({ page }) => {
  const writes = [];
  let pendingGroup;
  let fail = true;
  await mockApi(page, (url, request) => {
    if (url.pathname.endsWith("/groups/-1001")) return group(1);
    if (url.pathname.endsWith("/ai-route") && request.method() === "PUT") {
      writes.push({ path: url.pathname, body: request.postDataJSON() });
      return {};
    }
  });
  await page.route("**/groups/-1002", async (route) => {
    if (fail) { pendingGroup = route; return; }
    return route.fulfill({ json: group(2) });
  });
  await page.goto(fixtureUrl("detail"));
  const mode = page.getByRole("combobox", { name: /^AI mode / });
  await expect(mode).toHaveValue("inherit");
  await mode.selectOption("local_only");
  await page.getByRole("button", { name: "Other fixture group" }).click();
  await expect.poll(() => Boolean(pendingGroup)).toBe(true);
  // Exercise the dangerous save if obsolete controls survive the group switch.
  if (await mode.isVisible()) {
    await page.locator(".ops-form-body").filter({ has: mode }).getByRole("button").last().click();
  }
  expect(writes).toEqual([]);
  await expect(mode).toHaveCount(0);
  await expect(page.locator(".group-detail")).toHaveCount(0);
  await pendingGroup.fulfill({ status: 503, json: { detail: "Requested group unavailable" } });
  await expect(page.getByRole("alert")).toContainText("Requested group unavailable");
  await expect(mode).toHaveCount(0);
  fail = false;
  await page.getByRole("button", { name: "Thử lại", exact: true }).click();
  await expect(page.locator(".group-detail .ops-page-intro h2")).toHaveText("Fixture 02");
  await expect(mode).toHaveValue("inherit");
  expect(writes).toEqual([]);
});

test("group drafts survive refresh while authoritative permissions and untouched settings update", async ({ page }) => {
  let current = group();
  await mockApi(page, (url, request) => {
    if (/\/groups\/-100[12]$/.test(url.pathname)) {
      return url.pathname.endsWith("-1002") ? group(2) : current;
    }
    if (url.pathname.endsWith("/ai-route") && request.method() === "PUT") {
      current = { ...current, policy: { ...current.policy, ai_mode: request.postDataJSON().mode } };
      return current.policy;
    }
  });
  await page.goto(fixtureUrl("detail"));
  const mode = page.getByRole("combobox", { name: /^AI mode / });
  const topK = page.locator('input[type="number"][max="50"]');
  const retention = page.locator(".quota-grid input").first();
  await mode.selectOption("local_only");
  await topK.fill("9");
  await retention.fill("90");
  current = { ...current, permissions: { read_messages: false },
    policy: { ...current.policy, cloud_fallback: true } };
  await page.getByRole("button", { name: "Refresh fixture" }).click();
  await expect(page.getByRole("switch", { name: /read_messages/ })).toHaveAttribute("aria-checked", "false");
  await expect(mode).toHaveValue("local_only");
  await expect(topK).toHaveValue("9");
  await expect(retention).toHaveValue("90");
  await expect(page.getByRole("switch", { name: "Cloud fallback" })).toHaveAttribute("aria-checked", "true");
  await page.locator(".ops-form-body").filter({ has: mode }).getByRole("button").last().click();
  await expect.poll(() => current.policy.ai_mode).toBe("local_only");
  current = { ...current, policy: { ...current.policy, ai_mode: "cloud_only" } };
  await page.getByRole("button", { name: "Refresh fixture" }).click();
  await expect(mode).toHaveValue("cloud_only");
  await expect(topK).toHaveValue("9");
  await expect(retention).toHaveValue("90");
  await page.getByRole("button", { name: "Other fixture group" }).click();
  await expect(mode).toHaveValue("inherit");
  await expect(topK).toHaveValue("5");
  await expect(retention).toHaveValue("30");
});

for (const width of [390, 1024]) {
test(`navigation contains keyboard focus and restores it at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: width === 390 ? 844 : 768 });
  await mockApi(page);
  await page.goto(fixtureUrl("app"));
  const trigger = page.locator(".menu-button");
  await expect(trigger).toBeVisible();
  await trigger.click();
  await expect(page.locator(".sidebar nav button").first()).toBeFocused();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("Shift+Tab");
  await expect(page.locator(".sidebar .owner-card button")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.locator(".sidebar nav button").first()).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.locator(".sidebar")).toBeHidden();
  await expect(trigger).toBeFocused();
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await trigger.click();
  await page.locator(".sidebar nav button").nth(2).click();
  await expect(page.locator(".sidebar")).toBeHidden();
  await expect(trigger).toBeFocused();
  await expect(page.locator("main")).not.toHaveAttribute("inert");
  await trigger.click();
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.locator(".sidebar")).not.toHaveAttribute("role", "dialog");
  await expect(page.locator("main")).not.toHaveAttribute("inert");
  await expect(page.locator(".sidebar nav button").nth(2)).toHaveAttribute("aria-current", "page");
});
}

test("logging out from the mobile drawer releases it before the next owner session", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await page.goto(fixtureUrl("app"));
  await page.locator(".menu-button").click();
  await page.locator(".sidebar .owner-card button").click();
  await page.locator(".login-code-field input").fill("12345678");
  await page.locator(".login-submit").click();
  await expect(page.locator(".menu-button")).toBeVisible();
  await expect(page.locator(".sidebar")).toBeHidden();
  await expect(page.locator("main")).not.toHaveAttribute("inert");
});

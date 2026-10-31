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

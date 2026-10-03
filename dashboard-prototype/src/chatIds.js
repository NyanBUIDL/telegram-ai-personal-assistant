import { contractSchema } from "./contracts/generated.js";

const chatIdPattern = new RegExp(
  contractSchema.contracts.RevocationReport.properties.chat_id.pattern,
);

export function canonicalChatId(value, allowLegacyInteger = false) {
  if (allowLegacyInteger && Number.isSafeInteger(value) && value !== 0) {
    value = String(value);
  }
  const match = typeof value === "string" ? chatIdPattern.exec(value) : null;
  if (!match || match[0] !== value) {
    throw new TypeError("Chat ID không hợp lệ.");
  }
  return value;
}

export function canonicalChatIds(values, allowLegacyInteger = false) {
  if (!Array.isArray(values)) throw new TypeError("Chat ID không hợp lệ.");
  return values.map((value) => canonicalChatId(value, allowLegacyInteger));
}

export function normalizePreferenceChatIds(body) {
  const normalized = { ...body };
  for (const field of ["always_keep_chat_ids", "ignored_recommendation_chat_ids"]) {
    if (normalized[field] !== undefined) {
      normalized[field] = canonicalChatIds(normalized[field], true);
    }
  }
  return normalized;
}

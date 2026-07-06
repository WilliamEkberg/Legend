// Doc: Natural_Language_Code/Frontend/info_frontend.md
//
// Centralized, session-scoped storage for the LLM API key.
//
// The key is kept in `sessionStorage`, NOT `localStorage`: it must not survive on
// disk in plaintext between app launches. This is a hardening MITIGATION, not
// encryption — a fully secure store (e.g. tauri-plugin-stronghold or an OS
// keyring) is the intended future upgrade. All read/write sites (App launcher,
// MapView ticket generation, ChatPanel) go through this module so the storage
// backend can be swapped in one place.

const API_KEY_STORAGE = "legend:apiKey";

/** Read the API key for this app session ("" if unset). */
export function getApiKey(): string {
  try {
    return sessionStorage.getItem(API_KEY_STORAGE) ?? "";
  } catch {
    return "";
  }
}

/** Store the API key for this app session only (cleared when the app closes). */
export function storeApiKey(value: string): void {
  try {
    if (value) {
      sessionStorage.setItem(API_KEY_STORAGE, value);
    } else {
      sessionStorage.removeItem(API_KEY_STORAGE);
    }
  } catch {
    /* ignore */
  }
}

/**
 * One-time migration: remove any API key persisted in plaintext `localStorage`
 * by older builds. Safe to call on every startup.
 */
export function purgeLegacyApiKey(): void {
  try {
    localStorage.removeItem(API_KEY_STORAGE);
  } catch {
    /* ignore */
  }
}

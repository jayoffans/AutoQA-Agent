let language = 'zh-CN';
let messages = {};
export async function loadLocale(value) {
  language = ['zh-CN', 'en-US'].includes(value) ? value : 'zh-CN';
  const response = await fetch(`/locales/${language}.json`);
  if (!response.ok) throw Error('Locale unavailable');
  messages = await response.json();
  document.documentElement.lang = language;
  try { localStorage.setItem('autoqa-language', language); } catch {}
}
export function t(key, values = {}) {
  let text = messages[key] || key;
  for (const [name, value] of Object.entries(values)) text = text.replaceAll(`{${name}}`, String(value));
  return text;
}
export function locale() { return language; }
export function preferredLocale() {
  try { return localStorage.getItem('autoqa-language') || 'zh-CN'; } catch { return 'zh-CN'; }
}

export async function request(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail;
    const message = typeof detail === 'string' ? detail : Array.isArray(detail)
      ? detail.slice(0, 3).map(item => `${item.loc?.slice(1).join('.') || 'Template'}: ${item.msg}`).join('; ')
      : 'Could not complete the request. Please try again.';
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

export function jsonRequest(method, value) {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) };
}

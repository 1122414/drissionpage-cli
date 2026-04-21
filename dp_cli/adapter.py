from __future__ import annotations

from collections import OrderedDict

from dp_cli.models import ElementRecord, INTERACTIVE_LOCATOR

ELEMENT_METADATA_SCRIPT = """
function buildXPath(node) {
  if (!node || node.nodeType !== Node.ELEMENT_NODE) {
    return '';
  }
  if (node === document.body) {
    return '/html/body';
  }
  const segments = [];
  let current = node;
  while (current && current.nodeType === Node.ELEMENT_NODE) {
    if (current === document.documentElement) {
      segments.unshift('html');
      break;
    }
    let index = 1;
    let sibling = current.previousElementSibling;
    while (sibling) {
      if (sibling.tagName === current.tagName) {
        index += 1;
      }
      sibling = sibling.previousElementSibling;
    }
    segments.unshift(current.tagName.toLowerCase() + '[' + index + ']');
    current = current.parentElement;
  }
  return '/' + segments.join('/');
}
const attrs = {};
for (const attr of Array.from(this.attributes || [])) {
  attrs[attr.name] = attr.value;
}
return {
  xpath: buildXPath(this),
  tag: (this.tagName || '').toLowerCase(),
  text: (this.innerText || this.textContent || '').trim(),
  value: this.value || '',
  role: this.getAttribute('role') || '',
  element_id: this.id || '',
  name: this.getAttribute('name') || '',
  placeholder: this.getAttribute('placeholder') || '',
  href: this.getAttribute('href') || '',
  input_type: this.getAttribute('type') || ''
};
"""


class DrissionPageAdapter:
    def page_info(self, tab) -> dict:
        return {
            "url": getattr(tab, "url", None),
            "title": getattr(tab, "title", None),
            "tab_id": getattr(tab, "tab_id", None),
        }

    def open_url(self, tab, url: str) -> dict:
        tab.get(url)
        return self.page_info(tab)

    def interactive_elements(self, tab) -> list[ElementRecord]:
        return self._serialize_elements(tab.eles(INTERACTIVE_LOCATOR))

    def find_by_locator(self, tab, locator: str) -> list[ElementRecord]:
        return self._serialize_elements(tab.eles(locator))

    def find_by_text(self, tab, text: str) -> list[ElementRecord]:
        query = text.lower()
        candidates = self.interactive_elements(tab)
        results = []
        for candidate in candidates:
            haystack = " ".join(
                part for part in (
                    candidate.text,
                    candidate.value,
                    candidate.name,
                    candidate.placeholder,
                    candidate.href,
                    candidate.element_id,
                )
                if part
            ).lower()
            if query in haystack:
                results.append(candidate)
        return results

    def resolve(self, tab, locator: str):
        return tab.ele(locator)

    def click(self, element) -> None:
        element.click()

    def type_text(self, element, text: str) -> None:
        element.input(text, clear=True)

    def _serialize_elements(self, elements) -> list[ElementRecord]:
        records = OrderedDict()
        for element in elements:
            payload = element.run_js(ELEMENT_METADATA_SCRIPT)
            if not payload or not payload.get("xpath"):
                continue
            record = ElementRecord(**payload)
            records[record.xpath] = record
        return list(records.values())

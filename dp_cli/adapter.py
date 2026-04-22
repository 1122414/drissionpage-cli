from __future__ import annotations

from collections import OrderedDict

from dp_cli.models import (
    Bounds,
    ContextInfo,
    INTERACTIVE_LOCATOR,
    SNAPSHOT_DEFAULT_DEPTH,
    SnapshotNodeRecord,
    Visibility,
)

BODY_LOCATOR = "xpath:/html/body"

SNAPSHOT_SCRIPT = """
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

function compactText(value) {
  return (value || '').replace(/\\s+/g, ' ').trim();
}

function textByIds(value) {
  if (!value) return '';
  const texts = [];
  for (const id of value.split(/\\s+/)) {
    const node = document.getElementById(id);
    if (!node) continue;
    const text = compactText(node.innerText || node.textContent || '');
    if (text && !texts.includes(text)) {
      texts.push(text);
    }
  }
  return texts.join(' ');
}

function associatedLabel(node) {
  const labelledBy = textByIds(node.getAttribute('aria-labelledby'));
  if (labelledBy) return labelledBy;

  if (node.id) {
    const escaped = window.CSS && window.CSS.escape ? window.CSS.escape(node.id) : node.id;
    const labels = Array.from(document.querySelectorAll('label[for="' + escaped + '"]'))
      .map((item) => compactText(item.innerText || item.textContent || ''))
      .filter(Boolean);
    if (labels.length) return labels.join(' ');
  }

  const wrappingLabel = node.closest('label');
  if (wrappingLabel) {
    const text = compactText(wrappingLabel.innerText || wrappingLabel.textContent || '');
    if (text) return text;
  }
  return '';
}

function elementBounds(node) {
  const rect = node.getBoundingClientRect();
  return {
    x: Number(rect.x.toFixed(1)),
    y: Number(rect.y.toFixed(1)),
    width: Number(rect.width.toFixed(1)),
    height: Number(rect.height.toFixed(1))
  };
}

function isVisible(node) {
  const rect = node.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return false;
  const style = window.getComputedStyle(node);
  if (!style) return true;
  if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
  if (style.opacity === '0') return false;
  return true;
}

function isInViewport(node) {
  const rect = node.getBoundingClientRect();
  return rect.bottom > 0 && rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth;
}

function isEnabled(node) {
  if (node.disabled) return false;
  if (node.getAttribute('disabled') !== null) return false;
  if (node.getAttribute('aria-disabled') === 'true') return false;
  return true;
}

function hasPointerEvents(node) {
  const style = window.getComputedStyle(node);
  return !style || style.pointerEvents !== 'none';
}

function explicitRole(node) {
  return (node.getAttribute('role') || '').trim().toLowerCase();
}

function implicitRole(node) {
  const tag = (node.tagName || '').toLowerCase();
  const type = (node.getAttribute('type') || '').toLowerCase();
  if (tag === 'a' && node.getAttribute('href')) return 'link';
  if (tag === 'button') return 'button';
  if (tag === 'summary') return 'button';
  if (tag === 'textarea') return 'textbox';
  if (tag === 'select') return 'combobox';
  if (tag === 'option') return 'option';
  if (tag === 'nav') return 'navigation';
  if (tag === 'main') return 'main';
  if (tag === 'aside') return 'complementary';
  if (tag === 'header') return 'banner';
  if (tag === 'footer') return 'contentinfo';
  if (tag === 'form') return 'form';
  if (tag === 'dialog') return 'dialog';
  if (tag === 'ul' || tag === 'ol') return 'list';
  if (tag === 'li') return 'listitem';
  if (tag === 'table') return 'table';
  if (tag === 'thead' || tag === 'tbody' || tag === 'tfoot') return 'rowgroup';
  if (tag === 'tr') return 'row';
  if (tag === 'section') return 'region';
  if (tag === 'input') {
    if (type === 'checkbox') return 'checkbox';
    if (type === 'radio') return 'radio';
    if (type === 'button' || type === 'submit' || type === 'reset') return 'button';
    return 'textbox';
  }
  return '';
}

function computedRole(node) {
  return explicitRole(node) || implicitRole(node);
}

function visibleText(node) {
  return compactText(node.innerText || node.textContent || '');
}

function iconHint(node) {
  const raw = [
    node.id || '',
    node.className || '',
    node.getAttribute('name') || '',
    node.getAttribute('title') || '',
    node.getAttribute('aria-label') || ''
  ].join(' ').toLowerCase();
  if (raw.includes('search') || raw.includes('magnifier')) return 'search';
  if (raw.includes('next') || raw.includes('forward') || raw.includes('page-next')) return 'next';
  if (raw.includes('prev') || raw.includes('previous') || raw.includes('page-prev')) return 'previous';
  if (raw.includes('menu') || raw.includes('nav')) return 'menu';
  if (raw.includes('user') || raw.includes('avatar') || raw.includes('profile')) return 'user';
  if (raw.includes('close') || raw.includes('cancel')) return 'close';
  return '';
}

function genericName(role, tag, inputType) {
  if (role === 'button') return 'button';
  if (role === 'link') return 'link';
  if (role === 'textbox' || tag === 'textarea') return 'textbox';
  if (role === 'checkbox') return 'checkbox';
  if (role === 'radio') return 'radio';
  if (role === 'combobox') return 'combobox';
  if (tag === 'input') {
    return inputType ? 'input ' + inputType : 'input';
  }
  return role || tag || 'node';
}

function accessibleName(node) {
  const role = computedRole(node);
  const tag = (node.tagName || '').toLowerCase();
  const inputType = (node.getAttribute('type') || '').toLowerCase();
  const candidates = [
    node.getAttribute('aria-label'),
    textByIds(node.getAttribute('aria-labelledby')),
    associatedLabel(node),
    node.getAttribute('title'),
    node.getAttribute('alt')
  ];
  if (role === 'button' || role === 'link' || role === 'checkbox' || role === 'radio' || tag === 'button' || tag === 'a') {
    candidates.push(visibleText(node));
  }
  candidates.push(node.getAttribute('placeholder'));
  candidates.push(node.getAttribute('name'));
  for (const candidate of candidates) {
    const normalized = compactText(candidate);
    if (normalized) return normalized;
  }
  const hint = iconHint(node);
  if (hint) return hint;
  return genericName(role, tag, inputType);
}

function firstHeadingText(node) {
  const heading = node.querySelector('h1,h2,h3,h4,h5,h6,[role="heading"]');
  if (!heading) return '';
  return compactText(heading.innerText || heading.textContent || '');
}

function landmarkSelector() {
  return 'header,nav,main,aside,footer,form,dialog,' +
    '[role="banner"],[role="navigation"],[role="main"],[role="search"],' +
    '[role="complementary"],[role="dialog"],[role="contentinfo"]';
}

function namedContainer(node) {
  const candidates = [
    node.getAttribute('aria-label'),
    textByIds(node.getAttribute('aria-labelledby')),
    node.getAttribute('title'),
    firstHeadingText(node)
  ];
  for (const candidate of candidates) {
    const normalized = compactText(candidate);
    if (normalized) return normalized;
  }
  return computedRole(node) || (node.tagName || '').toLowerCase() || 'container';
}

function contextInfo(node) {
  const context = {
    landmark: '',
    heading: '',
    form: '',
    list: '',
    dialog: ''
  };
  const landmark = node.closest(landmarkSelector());
  if (landmark) context.landmark = namedContainer(landmark);
  const section = node.closest('section,main,article,form,[role="dialog"],dialog');
  if (section) context.heading = firstHeadingText(section);
  const form = node.closest('form,[role="search"]');
  if (form) context.form = namedContainer(form);
  const list = node.closest('ul,ol,[role="list"],table,[role="table"]');
  if (list) context.list = namedContainer(list);
  const dialog = node.closest('dialog,[role="dialog"]');
  if (dialog) context.dialog = namedContainer(dialog);
  return context;
}

function isInteractiveNode(node) {
  const role = computedRole(node);
  if (node.matches('a,button,input,textarea,select,summary,[onclick],[contenteditable="true"]')) return true;
  return ['button', 'link', 'textbox', 'checkbox', 'radio', 'tab', 'switch', 'combobox', 'option'].includes(role);
}

function isSemanticContainer(node) {
  const tag = (node.tagName || '').toLowerCase();
  const role = computedRole(node);
  if (['banner', 'navigation', 'main', 'search', 'complementary', 'dialog', 'contentinfo'].includes(role)) return true;
  if (['header', 'nav', 'main', 'aside', 'footer', 'form', 'dialog'].includes(tag)) return true;
  if (['list', 'table', 'rowgroup', 'toolbar', 'tablist'].includes(role)) return true;
  if (['ul', 'ol', 'table', 'thead', 'tbody', 'tfoot'].includes(tag)) return true;
  if (tag === 'section') {
    return Boolean(node.getAttribute('aria-label') || node.getAttribute('aria-labelledby') || firstHeadingText(node));
  }
  return false;
}

function nodeDepth(rootNode, node) {
  let depth = 0;
  let current = node;
  while (current && current !== rootNode) {
    current = current.parentElement;
    depth += 1;
  }
  return depth;
}

function nearestSemanticParent(rootNode, node) {
  let current = node.parentElement;
  while (current && current !== rootNode.parentElement) {
    if (current !== node && (isSemanticContainer(current) || isInteractiveNode(current))) {
      return current;
    }
    if (current === rootNode) break;
    current = current.parentElement;
  }
  return null;
}

const root = this;
const maxDepth = arguments[0];
const nodes = [];

function pushNode(node) {
  const visible = isVisible(node);
  const inViewport = isInViewport(node);
  const interactableNow = isInteractiveNode(node) && visible && inViewport && isEnabled(node) && hasPointerEvents(node);
  if (!visible) return;
  const role = computedRole(node);
  const parent = nearestSemanticParent(root, node);
  nodes.push({
    xpath: buildXPath(node),
    parent_xpath: parent ? buildXPath(parent) : null,
    ref_type: isInteractiveNode(node) ? 'element' : 'container',
    tag: (node.tagName || '').toLowerCase(),
    role: role,
    name: isInteractiveNode(node) ? accessibleName(node) : namedContainer(node),
    text: visibleText(node),
    value: node.value || '',
    element_id: node.id || '',
    placeholder: node.getAttribute('placeholder') || '',
    href: node.getAttribute('href') || '',
    input_type: node.getAttribute('type') || '',
    title: node.getAttribute('title') || '',
    aria_label: node.getAttribute('aria-label') || '',
    alt: node.getAttribute('alt') || '',
    label: associatedLabel(node),
    depth: nodeDepth(root, node),
    bounds: elementBounds(node),
    visibility: {
      visible: visible,
      in_viewport: inViewport,
      interactable_now: interactableNow
    },
    context: contextInfo(node),
    disabled: !isEnabled(node),
    checked: !!node.checked || node.getAttribute('aria-checked') === 'true',
    selected: !!node.selected || node.getAttribute('aria-selected') === 'true',
    expanded: node.getAttribute('aria-expanded') === 'true'
  });
}

if ((isSemanticContainer(root) || isInteractiveNode(root)) && root !== document.body) {
  pushNode(root);
}

for (const node of Array.from(root.querySelectorAll('*'))) {
  const depth = nodeDepth(root, node);
  if (maxDepth !== null && maxDepth !== undefined && maxDepth >= 0 && depth > maxDepth) continue;
  if (isSemanticContainer(node) || isInteractiveNode(node)) {
    pushNode(node);
  }
}

return nodes;
"""

ELEMENT_STATE_SCRIPT = """
function compactText(value) {
  return (value || '').replace(/\\s+/g, ' ').trim();
}

function elementBounds(node) {
  const rect = node.getBoundingClientRect();
  return {
    x: Number(rect.x.toFixed(1)),
    y: Number(rect.y.toFixed(1)),
    width: Number(rect.width.toFixed(1)),
    height: Number(rect.height.toFixed(1))
  };
}

function isVisible(node) {
  const rect = node.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return false;
  const style = window.getComputedStyle(node);
  if (!style) return true;
  if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
  if (style.opacity === '0') return false;
  return true;
}

function isInViewport(node) {
  const rect = node.getBoundingClientRect();
  return rect.bottom > 0 && rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth;
}

function isEnabled(node) {
  if (node.disabled) return false;
  if (node.getAttribute('disabled') !== null) return false;
  if (node.getAttribute('aria-disabled') === 'true') return false;
  return true;
}

function hasPointerEvents(node) {
  const style = window.getComputedStyle(node);
  return !style || style.pointerEvents !== 'none';
}

return {
  text: compactText(this.innerText || this.textContent || ''),
  bounds: elementBounds(this),
  visible: isVisible(this),
  in_viewport: isInViewport(this),
  enabled: isEnabled(this),
  interactable_now: isVisible(this) && isInViewport(this) && isEnabled(this) && hasPointerEvents(this)
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

    def snapshot_nodes(self, tab, root_xpath: str | None = None, depth: int | None = None) -> list[SnapshotNodeRecord]:
        root = tab.ele(f"xpath:{root_xpath}") if root_xpath else tab.ele(BODY_LOCATOR)
        max_depth = depth if depth is not None else -1
        payload = root.run_js(SNAPSHOT_SCRIPT, max_depth)
        return self._serialize_snapshot_payloads(payload)

    def interactive_elements(self, tab) -> list[SnapshotNodeRecord]:
        return [node for node in self.snapshot_nodes(tab, depth=None) if node.ref_type == "element"]

    def find_by_locator(self, tab, locator: str) -> list[SnapshotNodeRecord]:
        return self._serialize_elements(tab.eles(locator))

    def find_by_text(self, tab, text: str) -> list[SnapshotNodeRecord]:
        query = text.lower()
        candidates = self.interactive_elements(tab)
        return [candidate for candidate in candidates if query in self._searchable_text(candidate)]

    def resolve(self, tab, locator: str):
        return tab.ele(locator)

    def element_state(self, element) -> dict:
        return element.run_js(ELEMENT_STATE_SCRIPT)

    def scroll_into_view(self, element) -> None:
        element.run_js("this.scrollIntoView({block: 'center', inline: 'center'});")

    def click(self, element) -> None:
        element.click()

    def type_text(self, element, text: str) -> None:
        element.input(text, clear=True)

    def _serialize_elements(self, elements) -> list[SnapshotNodeRecord]:
        records: OrderedDict[str, SnapshotNodeRecord] = OrderedDict()
        for element in elements:
            payload = element.run_js(SNAPSHOT_SCRIPT, 0)
            for item in payload if isinstance(payload, list) else [payload]:
                if not item or not item.get("xpath") or item.get("ref_type") != "element":
                    continue
                record = self._snapshot_record(item)
                records[record.xpath] = record
        return list(records.values())

    def _serialize_snapshot_payloads(self, payloads) -> list[SnapshotNodeRecord]:
        records: OrderedDict[str, SnapshotNodeRecord] = OrderedDict()
        for payload in payloads or []:
            if not payload or not payload.get("xpath"):
                continue
            record = self._snapshot_record(payload)
            records[record.xpath] = record
        return list(records.values())

    def _snapshot_record(self, payload: dict) -> SnapshotNodeRecord:
        if isinstance(payload.get("bounds"), dict):
            payload["bounds"] = Bounds(**payload["bounds"])
        if isinstance(payload.get("visibility"), dict):
            payload["visibility"] = Visibility(**payload["visibility"])
        if isinstance(payload.get("context"), dict):
            payload["context"] = ContextInfo(**payload["context"])
        return SnapshotNodeRecord(**payload)

    def _searchable_text(self, candidate: SnapshotNodeRecord) -> str:
        return " ".join(
            part
            for part in (
                candidate.name,
                candidate.text,
                candidate.label,
                candidate.value,
                candidate.placeholder,
                candidate.href,
                candidate.element_id,
                candidate.title,
                candidate.aria_label,
                candidate.context.heading,
                candidate.context.landmark,
            )
            if part
        ).lower()

from __future__ import annotations

from collections import OrderedDict

from dp_cli.models import (
    Bounds,
    ContextInfo,
    SnapshotNodeRecord,
    Visibility,
)

BODY_LOCATOR = "xpath:/html/body"

SCROLL_METRICS_SCRIPT = """
(() => {
  const root = document.scrollingElement || document.documentElement;
  const x = Number(window.scrollX || root.scrollLeft || 0);
  const y = Number(window.scrollY || root.scrollTop || 0);
  const viewportWidth = Number(window.innerWidth || root.clientWidth || 0);
  const viewportHeight = Number(window.innerHeight || root.clientHeight || 0);
  const scrollWidth = Number(root.scrollWidth || 0);
  const scrollHeight = Number(root.scrollHeight || 0);
  return {
    x,
    y,
    viewport_width: viewportWidth,
    viewport_height: viewportHeight,
    scroll_width: scrollWidth,
    scroll_height: scrollHeight,
    at_top: y <= 1,
    at_bottom: y + viewportHeight >= scrollHeight - 2
  };
})()
"""

SHARED_JS_HELPERS = """
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

function classText(node) {
  const raw = node.className || '';
  if (typeof raw === 'string') return raw;
  if (raw && typeof raw.baseVal === 'string') return raw.baseVal;
  return '';
}

function classTokens(node) {
  return classText(node).toLowerCase().split(/[\s_-]+/).filter(Boolean);
}

function checkboxRelatedNodes(node) {
  const nodes = [];
  let current = node;
  for (let i = 0; current && i < 5; i += 1) {
    nodes.push(current);
    current = current.parentElement;
  }
  const label = node.closest && node.closest('label');
  if (label && !nodes.includes(label)) nodes.push(label);
  return nodes;
}

function isCheckboxish(node) {
  const tag = (node.tagName || '').toLowerCase();
  const type = (node.getAttribute('type') || '').toLowerCase();
  const role = (node.getAttribute('role') || '').toLowerCase();
  const classes = classText(node).toLowerCase();
  if (tag === 'input' && (type === 'checkbox' || type === 'radio')) return true;
  if (role === 'checkbox' || role === 'radio' || role === 'switch') return true;
  return classes.includes('checkbox') || classes.includes('check-box') || classes.includes('check_box');
}

function relatedCheckInput(node) {
  const tag = (node.tagName || '').toLowerCase();
  const type = (node.getAttribute('type') || '').toLowerCase();
  if (tag === 'input' && (type === 'checkbox' || type === 'radio')) return node;

  const selectors = 'input[type="checkbox"],input[type="radio"]';
  if (node.querySelector) {
    const child = node.querySelector(selectors);
    if (child) return child;
  }
  const label = node.closest && node.closest('label');
  if (label && label.querySelector) {
    const labelled = label.querySelector(selectors);
    if (labelled) return labelled;
  }
  return null;
}

function checkedState(node) {
  const input = relatedCheckInput(node);
  if (input === node && typeof input.checked === 'boolean') return !!input.checked;

  for (const candidate of checkboxRelatedNodes(node)) {
    const aria = candidate.getAttribute && candidate.getAttribute('aria-checked');
    if (aria === 'true' || aria === 'mixed') return true;
    if (aria === 'false') return false;
  }

  for (const candidate of checkboxRelatedNodes(node)) {
    if (!isCheckboxish(candidate)) continue;
    const tokens = classTokens(candidate);
    if (tokens.some(token => ['unchecked', 'uncheck', 'disabled'].includes(token))) continue;
    if (tokens.some(token => ['checked', 'selected', 'active', 'on'].includes(token))) return true;
  }
  if (input && typeof input.checked === 'boolean') return !!input.checked;
  return false;
}
"""

SNAPSHOT_SCRIPT = SHARED_JS_HELPERS + """
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

function nearbyOwnText(node) {
  const parts = [];
  let current = node.parentElement;
  for (let i = 0; current && i < 2; i += 1) {
    const text = compactText(current.innerText || current.textContent || '');
    if (text) parts.push(text);
    current = current.parentElement;
  }
  return parts.join(' ');
}

function inputFieldHint(node) {
  const tag = (node.tagName || '').toLowerCase();
  if (tag !== 'input' && tag !== 'textarea') return '';
  const type = (node.getAttribute('type') || '').toLowerCase();
  const raw = [
    node.id || '',
    classText(node),
    node.getAttribute('name') || '',
    node.getAttribute('placeholder') || '',
    node.getAttribute('aria-label') || '',
    node.getAttribute('autocomplete') || '',
    node.getAttribute('inputmode') || '',
    nearbyOwnText(node)
  ].join(' ').toLowerCase();
  if (type === 'password' || raw.includes('password') || raw.includes('pwd') || raw.includes('密码')) return '密码';
  if (raw.includes('验证码') || raw.includes('captcha') || raw.includes('verify') || raw.includes('verification') || raw.includes('获取验证码')) return '验证码';
  if (type === 'tel' || raw.includes('phone') || raw.includes('mobile') || raw.includes('tel') || raw.includes('手机号') || raw.includes('手机')) return '手机号';
  const maxLength = Number(node.getAttribute('maxlength') || node.maxLength || 0);
  if (maxLength === 11) return '手机号';
  const form = node.closest('form');
  if (form) {
    const formText = compactText(form.innerText || form.textContent || '');
    const inputs = Array.from(form.querySelectorAll('input,textarea')).filter(item => {
      const itemType = (item.getAttribute('type') || '').toLowerCase();
      return itemType !== 'hidden' && isVisible(item);
    });
    const index = inputs.indexOf(node);
    if (formText.includes('注册') && formText.includes('获取验证码') && inputs.length >= 4) {
      if (index === 0) return '昵称';
      if (index === 1) return '手机号';
      if (index === 2) return '验证码';
      if (index === 3) return '密码';
    }
  }
  if (raw.includes('nickname') || raw.includes('nick') || raw.includes('user') || raw.includes('name') || raw.includes('昵称') || raw.includes('用户名')) return '昵称';
  return '';
}

function agreementText(node) {
  const parentText = compactText((node.parentElement && (node.parentElement.innerText || node.parentElement.textContent)) || '');
  const grandText = compactText((node.parentElement && node.parentElement.parentElement && (node.parentElement.parentElement.innerText || node.parentElement.parentElement.textContent)) || '');
  for (const text of [parentText, grandText]) {
    if ((text.includes('同意') || text.includes('阅读') || text.includes('接受')) && (text.includes('协议') || text.includes('隐私') || text.includes('条款'))) {
      return text;
    }
  }
  return '';
}

function explicitRole(node) {
  return (node.getAttribute('role') || '').trim().toLowerCase();
}

function implicitRole(node) {
  const tag = (node.tagName || '').toLowerCase();
  const type = (node.getAttribute('type') || '').toLowerCase();
  const className = classText(node).toLowerCase();
  const classes = classTokens(node);
  if (className.includes('checkbox') || className.includes('check-box') || className.includes('check_box')) return 'checkbox';
  if (
    classes.includes('tabitem') ||
    (classes.includes('tab') && classes.includes('item')) ||
    className.includes('ant-tabs-tab')
  ) return 'tab';
  if (
    (classes.includes('tabs') && (classes.includes('nav') || classes.includes('bar') || classes.includes('wrap'))) ||
    className.includes('ant-tabs-nav')
  ) return 'tablist';
  if (tag === 'a' && node.getAttribute('href')) return 'link';
  if (tag === 'button') return 'button';
  if (tag === 'summary') return 'button';
  if (tag === 'textarea') return 'textbox';
  if (tag === 'select') return 'combobox';
  if (tag === 'option') return 'option';
  const text = visibleText(node);
  if ((tag === 'span' || tag === 'div') && /^(获取验证码|发送验证码|重新发送|换一换|提交|确认)$/.test(text)) return 'button';
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
  if (node.getAttribute('aria-selected') !== null && node.closest('[role="tablist"],[class*="tabs"]')) return 'tab';
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
    classText(node),
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
    inputFieldHint(node),
    role === 'checkbox' ? agreementText(node) : '',
    node.getAttribute('aria-label'),
    textByIds(node.getAttribute('aria-labelledby')),
    associatedLabel(node),
    node.getAttribute('title'),
    node.getAttribute('alt')
  ];
  if (role === 'button' || role === 'link' || role === 'tab' || role === 'checkbox' || role === 'radio' || tag === 'button' || tag === 'a') {
    candidates.push(visibleText(node));
  }
  // For elements that appear interactive via cursor, tabindex, or class patterns, also use visible text
  const classes = classTokens(node);
  const hasClickableStyle = (() => {
    try {
      const style = window.getComputedStyle ? window.getComputedStyle(node) : null;
      if (style && style.cursor === 'pointer') return true;
    } catch (e) {}
    const tabindex = node.getAttribute('tabindex');
    if (tabindex !== null && tabindex !== '-1') return true;
    if (classes.some(c => /^(icon|btn|button|search|submit|close|menu|toggle|tab|tabitem)$/.test(c))) return true;
    return false;
  })();
  if (hasClickableStyle) {
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
  const tag = (node.tagName || '').toLowerCase();
  if (node.matches && node.matches('a,button,input,textarea,select,summary,[onclick],[contenteditable="true"]')) return true;
  if (['button', 'link', 'textbox', 'checkbox', 'radio', 'tab', 'switch', 'combobox', 'option'].includes(role)) return true;
  // Icon/button spans and i tags with common class patterns
  const classes = classTokens(node);
  if (classes.some(c => /^(icon|btn|button|search|submit|close|menu|toggle|tab|tabitem)$/.test(c))) return true;
  // Elements with pointer cursor are likely interactive (common for tab switches, custom buttons)
  try {
    const style = window.getComputedStyle ? window.getComputedStyle(node) : null;
    if (style && style.cursor === 'pointer') return true;
  } catch (e) {}
  // Elements with explicit positive tabindex are likely interactive
  const tabindex = node.getAttribute('tabindex');
  if (tabindex !== null && tabindex !== '-1') return true;
  return false;
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

function computeSemanticLevel(node, depth, isSemantic, isInteractive, inViewport, interactableNow, role) {
  if (interactableNow) return 'surface';
  const dataContainerRoles = {'table': true, 'list': true, 'grid': true, 'rowgroup': true, 'row': true};
  if (dataContainerRoles[role]) return 'surface';
  if (isSemantic && inViewport) return 'surface';
  if (inViewport && depth <= 3) return 'surface';
  return 'deep';
}

function pushNode(node) {
  const visible = isVisible(node);
  const inViewport = isInViewport(node);
  const interactableNow = isInteractiveNode(node) && visible && inViewport && isEnabled(node) && hasPointerEvents(node);
  if (!visible) return;
  const role = computedRole(node);
  const isSemantic = isSemanticContainer(node);
  const isInteractive = isInteractiveNode(node);
  const depth = nodeDepth(root, node);
  const parent = nearestSemanticParent(root, node);
  const semanticLevel = computeSemanticLevel(node, depth, isSemantic, isInteractive, inViewport, interactableNow, role);
  const tag = (node.tagName || '').toLowerCase();
  nodes.push({
    xpath: buildXPath(node),
    parent_xpath: parent ? buildXPath(parent) : null,
    ref_type: isInteractive ? 'element' : 'container',
    tag: tag,
    role: role,
    name: isInteractive ? accessibleName(node) : namedContainer(node),
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
    depth: depth,
    bounds: elementBounds(node),
    visibility: {
      visible: visible,
      in_viewport: inViewport,
      interactable_now: interactableNow
    },
    context: contextInfo(node),
    semantic_level: semanticLevel,
    disabled: !isEnabled(node),
    checked: checkedState(node),
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
  pushNode(node);
}

return nodes;
"""

ELEMENT_STATE_SCRIPT = SHARED_JS_HELPERS + """
return {
  text: compactText(this.innerText || this.textContent || ''),
  value: this.value || '',
  bounds: elementBounds(this),
  visible: isVisible(this),
  in_viewport: isInViewport(this),
  enabled: isEnabled(this),
  interactable_now: isVisible(this) && isInViewport(this) && isEnabled(this) && hasPointerEvents(this),
  checked: checkedState(this),
  selected: !!this.selected || this.getAttribute('aria-selected') === 'true',
  expanded: this.getAttribute('aria-expanded') === 'true'
};
"""

DETAIL_EXTRACTION_SCRIPT = SHARED_JS_HELPERS + """
(() => {
  const text = node => compactText((node && (node.innerText || node.textContent)) || '');
  const attr = (node, name) => (node && node.getAttribute && node.getAttribute(name)) || '';
  const visibleElements = selector => Array.from(document.querySelectorAll(selector)).filter(isVisible);
  const metaContent = selector => attr(document.querySelector(selector), 'content').trim();
  const semanticRoot = document.querySelector('main, article, [role="main"]') || document.body;
  const isNoiseScope = node => !!(node && node.closest && node.closest(
    'nav, header, footer, aside, [role="navigation"], [role="contentinfo"], ' +
    '[class*="recommend"], [class*="related"], [class*="sidebar"], [class*="catalog"], ' +
    '[class*="comment"], [id*="recommend"], [id*="related"], [id*="sidebar"], ' +
    '[id*="catalog"], [id*="comment"]'
  ));
  const jsonLdObjects = [];
  for (const script of Array.from(document.querySelectorAll('script[type="application/ld+json"]'))) {
    try {
      const parsed = JSON.parse(script.textContent || '{}');
      const values = Array.isArray(parsed) ? parsed : [parsed];
      for (const value of values) {
        if (!value || typeof value !== 'object') continue;
        jsonLdObjects.push(value);
        if (Array.isArray(value['@graph'])) jsonLdObjects.push(...value['@graph']);
      }
    } catch (_) {}
  }
  const structured = jsonLdObjects.find(value =>
    value && typeof value === 'object' &&
    (value.name || value.headline || value.description || value.image)
  ) || {};
  const structuredImage = value => {
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) return structuredImage(value[0]);
    if (value && typeof value === 'object') return value.url || value.contentUrl || '';
    return '';
  };
  const firstText = selectors => {
    for (const selector of selectors) {
      for (const node of visibleElements(selector)) {
        if (isNoiseScope(node)) continue;
        const value = text(node);
        if (value) return value;
      }
    }
    return '';
  };

  const title = compactText(
    structured.name || structured.headline ||
    metaContent('meta[property="og:title"]') ||
    metaContent('meta[name="twitter:title"]')
  ) || firstText([
    'main h1',
    'article h1',
    '[role="main"] h1',
    'h1',
    'main .title',
    'article .title',
    '.vodh h2',
    '.vodh h1'
  ]) || compactText(document.title || '');

  const labels = [
    ['director', ['director', '\\u5bfc\\u6f14']],
    ['actors', ['actor', 'actors', 'cast', '\\u4e3b\\u6f14', '\\u6f14\\u5458']],
    ['category', ['category', 'genre', 'type', '\\u7c7b\\u578b']],
    ['region', ['region', 'area', '\\u5730\\u533a']],
    ['year', ['year', '\\u5e74\\u4efd', '\\u5e74\\u4ee3']],
    ['language', ['language', '\\u8bed\\u8a00']],
    ['release_date', ['release', '\\u4e0a\\u6620', '\\u53d1\\u884c']],
    ['updated_at', ['update', 'updated', '\\u66f4\\u65b0', '\\u66f4\\u65b0\\u65f6\\u95f4']]
  ];

  const detailInfo = {};
  if (title) detailInfo.title = title;
  const lines = text(document.body).split(/\\n+/).map(line => compactText(line)).filter(Boolean);
  for (const line of lines) {
    if (line.length > 180) continue;
    for (const [field, names] of labels) {
      if (detailInfo[field]) continue;
      for (const name of names) {
        const index = line.toLowerCase().indexOf(name.toLowerCase());
        if (index < 0) continue;
        let value = line.slice(index + name.length).replace(/^[\\s:：\\-]+/, '').trim();
        if (!value && line.includes('：')) value = line.split('：').slice(1).join('：').trim();
        if (!value && line.includes(':')) value = line.split(':').slice(1).join(':').trim();
        if (value && value.length <= 160) {
          detailInfo[field] = value;
          break;
        }
      }
    }
  }

  const descSelectors = [
    '[class*="desc"]',
    '[id*="desc"]',
    '[class*="content"]',
    '[id*="content"]',
    '[class*="plot"]',
    '[class*="intro"]',
    '.vod_content',
    '.vodplayinfo'
  ];
  let description = '';
  for (const selector of descSelectors) {
    for (const node of visibleElements(selector)) {
      const value = text(node);
      if (value && value.length > description.length) description = value;
    }
  }
  if (!description) {
    for (const node of visibleElements('p')) {
      const value = text(node);
      if (value.length > description.length) description = value;
    }
  }
  if (description) detailInfo.description = description.slice(0, 3000);

  const imageCandidates = visibleElements('img')
    .map(node => ({
      src: node.currentSrc || attr(node, 'src') || attr(node, 'data-src') || attr(node, 'data-original'),
      alt: attr(node, 'alt'),
      area: Math.max(1, node.getBoundingClientRect().width * node.getBoundingClientRect().height)
    }))
    .filter(item => item.src);
  imageCandidates.sort((a, b) => b.area - a.area);
  if (imageCandidates[0]) {
    detailInfo.cover = new URL(imageCandidates[0].src, location.href).href;
    if (imageCandidates[0].alt && !detailInfo.title) detailInfo.title = imageCandidates[0].alt;
  }

  const playUrls = visibleElements('a[href]')
    .map(node => ({
      text: text(node),
      url: new URL(attr(node, 'href'), location.href).href
    }))
    .filter(item => {
      const haystack = (item.text + ' ' + item.url).toLowerCase();
      return /play|m3u8|episode|ep\\d+|vod-play|\\u64ad\\u653e|\\u7b2c\\d+/.test(haystack);
    });
  const seenPlayUrls = new Set();
  detailInfo.play_urls = playUrls.filter(item => {
    if (seenPlayUrls.has(item.url)) return false;
    seenPlayUrls.add(item.url);
    return true;
  }).slice(0, 100);

  const fields = Object.keys(detailInfo);
  return {
    source_url: location.href,
    page_title: compactText(document.title || ''),
    fields,
    detail_info: detailInfo,
    template: {
      extract_strategy: 'dp_cli_detail_js_v1',
      fields,
      selectors: {
        title: 'h1, .title, [class*=title], [class*=name], .vodh h2, .vodh h1',
        meta: 'body text label scan',
        description: descSelectors.join(', '),
        cover: 'largest visible img',
        play_urls: 'a[href] filtered by play keywords'
      }
    }
  };
})()
"""

DETAIL_PAGE_PACKAGE_SCRIPT = SHARED_JS_HELPERS + """
(() => {
  const text = node => compactText((node && (node.innerText || node.textContent)) || '');
  const attr = (node, name) => (node && node.getAttribute && node.getAttribute(name)) || '';
  const visibleElements = selector => Array.from(document.querySelectorAll(selector)).filter(isVisible);
  const truncate = (value, size) => {
    const compact = compactText(value || '');
    return compact.length > size ? compact.slice(0, size) : compact;
  };

  const headings = visibleElements('h1,h2,h3')
    .map(node => ({tag: (node.tagName || '').toLowerCase(), text: truncate(text(node), 180)}))
    .filter(item => item.text)
    .slice(0, 30);

  const links = visibleElements('a[href]')
    .map(node => ({
      text: truncate(text(node), 160),
      url: new URL(attr(node, 'href'), location.href).href
    }))
    .filter(item => item.text || item.url)
    .slice(0, 80);

  const images = visibleElements('img')
    .map(node => ({
      alt: truncate(attr(node, 'alt'), 120),
      src: node.currentSrc || attr(node, 'src') || attr(node, 'data-src') || attr(node, 'data-original'),
      area: Math.round(Math.max(1, node.getBoundingClientRect().width * node.getBoundingClientRect().height))
    }))
    .filter(item => item.src)
    .sort((a, b) => b.area - a.area)
    .slice(0, 20)
    .map(item => ({alt: item.alt, src: new URL(item.src, location.href).href}));

  const tables = visibleElements('table')
    .map(table => Array.from(table.querySelectorAll('tr')).slice(0, 12).map(row =>
      Array.from(row.querySelectorAll('th,td')).slice(0, 8).map(cell => truncate(text(cell), 120))
    ).filter(row => row.some(Boolean)))
    .filter(rows => rows.length)
    .slice(0, 5);

  const mainNode = document.querySelector('main, article, [role="main"]') || document.body;
  return {
    url: location.href,
    title: compactText(document.title || ''),
    headings,
    main_text: truncate(text(mainNode), 9000),
    body_text: truncate(text(document.body), 12000),
    links,
    images,
    tables
  };
})()
"""

PREFERRED_CLICK_OFFSET_SCRIPT = SHARED_JS_HELPERS + """
function localClassText(node) {
  const raw = node.className || '';
  if (typeof raw === 'string') return raw;
  if (raw && typeof raw.baseVal === 'string') return raw.baseVal;
  return '';
}

function localButtonish(node) {
  const tag = (node.tagName || '').toLowerCase();
  const role = (node.getAttribute('role') || '').toLowerCase();
  const type = (node.getAttribute('type') || '').toLowerCase();
  const classes = localClassText(node).toLowerCase();
  const text = compactText(node.innerText || node.textContent || '');
  if (['button', 'a', 'summary'].includes(tag)) return true;
  if (tag === 'input' && ['button', 'submit', 'reset', 'checkbox', 'radio'].includes(type)) return true;
  if (['button', 'link', 'checkbox', 'radio', 'tab', 'switch'].includes(role)) return true;
  if (classes.includes('checkbox') || classes.includes('check-box') || classes.includes('check_box')) return true;
  if ((tag === 'span' || tag === 'div') && /^(获取验证码|发送验证码|重新发送|换一换|提交|确认|注册)$/.test(text)) return true;
  return false;
}

function localBounds(node) {
  const rect = node.getBoundingClientRect();
  return {left: rect.left, top: rect.top, width: rect.width, height: rect.height};
}

const root = this;
const rootRect = localBounds(root);
const rootText = compactText(root.innerText || root.textContent || '');
const rootArea = Math.max(rootRect.width * rootRect.height, 1);
let best = null;
let bestScore = -Infinity;

for (const node of Array.from(root.querySelectorAll('*'))) {
  if (!isVisible(node) || !isEnabled(node) || !hasPointerEvents(node)) continue;
  if (!localButtonish(node)) continue;
  const rect = localBounds(node);
  if (rect.width <= 0 || rect.height <= 0) continue;
  const text = compactText(node.innerText || node.textContent || '');
  const tag = (node.tagName || '').toLowerCase();
  const role = (node.getAttribute('role') || '').toLowerCase();
  const area = Math.max(rect.width * rect.height, 1);
  const sameText = text && (rootText === text || rootText.includes(text));
  const muchSmaller = area < rootArea * 0.75;
  if (!sameText && !muchSmaller && tag !== 'button' && tag !== 'a' && role !== 'button') continue;
  let score = 0;
  if (sameText) score += 100;
  if (muchSmaller) score += 60;
  if (tag === 'button' || tag === 'a') score += 50;
  if (tag === 'span') score += 40;
  if (role === 'button' || role === 'checkbox') score += 40;
  if (/获取验证码|发送验证码|重新发送/.test(text)) score += 120;
  if (/checkbox|check-box|check_box/.test(localClassText(node).toLowerCase())) score += 80;
  score -= area / rootArea;
  if (score > bestScore) {
    best = node;
    bestScore = score;
  }
}

if (!best) return null;
const bestRect = localBounds(best);
return {
  offset_x: Number((bestRect.left - rootRect.left + bestRect.width / 2).toFixed(1)),
  offset_y: Number((bestRect.top - rootRect.top + bestRect.height / 2).toFixed(1)),
  target_text: compactText(best.innerText || best.textContent || ''),
  target_tag: (best.tagName || '').toLowerCase()
};
"""


class DrissionPageAdapter:
    def page_info(self, tab) -> dict:
        return {
            "url": getattr(tab, "url", None),
            "title": getattr(tab, "title", None),
            "tab_id": getattr(tab, "tab_id", None),
        }

    def open_url(self, tab, url: str, timeout: float | None = None) -> dict:
        if timeout is not None and timeout > 0:
            tab.get(url, timeout=timeout)
        else:
            tab.get(url)
        return self.page_info(tab)

    def wait_ready(
        self,
        tab,
        *,
        condition: str,
        locator: str | None = None,
        timeout: float | None = None,
        listener_started: bool = False,
    ) -> bool:
        """Use DrissionPage's native waits; never substitute a blind sleep."""
        normalized = str(condition or "document").lower().replace("_", "-")
        if normalized == "document":
            return bool(tab.wait.doc_loaded(timeout=timeout))
        if normalized == "element":
            if not locator:
                raise ValueError("element readiness requires a locator")
            return bool(tab.wait.eles_loaded(locator, timeout=timeout))
        if normalized == "network-idle":
            if not listener_started:
                tab.listen.start(True)
            try:
                return bool(tab.listen.wait_silent(timeout=timeout, targets_only=False))
            finally:
                tab.listen.stop()
        raise ValueError(f"unsupported readiness condition: {condition}")

    def snapshot_nodes(self, tab, root_xpath: str | None = None, depth: int | None = None) -> list[SnapshotNodeRecord]:
        if root_xpath:
            root = tab.ele(f"xpath:{root_xpath}")
        else:
            try:
                root = tab.ele(BODY_LOCATOR)
            except Exception:
                tab.wait.ele_displayed(BODY_LOCATOR, timeout=10)
                root = tab.ele(BODY_LOCATOR)
        max_depth = depth if depth is not None else -1
        payload = root.run_js(SNAPSHOT_SCRIPT, max_depth)
        return self._serialize_snapshot_payloads(payload)

    def find_by_locator(self, tab, locator: str) -> list[SnapshotNodeRecord]:
        return self._serialize_elements(tab.eles(locator), require_element_type=False)

    def resolve(self, tab, locator: str):
        return tab.ele(locator)

    def element_state(self, element) -> dict:
        return element.run_js(ELEMENT_STATE_SCRIPT)

    def extract_detail(self, tab) -> dict:
        return tab.run_js(DETAIL_EXTRACTION_SCRIPT, as_expr=True)

    def detail_page_package(self, tab) -> dict:
        return tab.run_js(DETAIL_PAGE_PACKAGE_SCRIPT, as_expr=True)

    def scroll_into_view(self, element) -> None:
        try:
            element.scroll.to_see(center=True)
            return
        except Exception:
            element.run_js(
                "this.scrollIntoView({block: 'center', inline: 'center'});"
            )

    def scroll_metrics(self, tab) -> dict:
        result = tab.run_js(SCROLL_METRICS_SCRIPT, as_expr=True)
        return result if isinstance(result, dict) else {}

    def scroll_page(
        self,
        tab,
        *,
        direction: str = "down",
        amount: int = 900,
        to: str | None = None,
    ) -> None:
        normalized_direction = str(direction or "down").lower()
        normalized_to = str(to or "").lower()
        try:
            if normalized_to == "top":
                tab.scroll.to_top()
            elif normalized_to == "bottom":
                tab.scroll.to_bottom()
            elif normalized_to == "half":
                tab.scroll.to_half()
            elif normalized_to == "leftmost":
                tab.scroll.to_leftmost()
            elif normalized_to == "rightmost":
                tab.scroll.to_rightmost()
            elif normalized_direction == "up":
                tab.scroll.up(amount)
            elif normalized_direction == "left":
                tab.scroll.left(amount)
            elif normalized_direction == "right":
                tab.scroll.right(amount)
            else:
                tab.scroll.down(amount)
            return
        except Exception:
            pass

        if normalized_to == "top":
            tab.run_js("window.scrollTo(window.scrollX, 0);")
        elif normalized_to == "bottom":
            tab.run_js(
                "window.scrollTo(window.scrollX, "
                "(document.scrollingElement || document.documentElement).scrollHeight);"
            )
        elif normalized_to == "half":
            tab.run_js(
                "window.scrollTo(window.scrollX, "
                "(document.scrollingElement || document.documentElement).scrollHeight / 2);"
            )
        elif normalized_to == "leftmost":
            tab.run_js("window.scrollTo(0, window.scrollY);")
        elif normalized_to == "rightmost":
            tab.run_js(
                "window.scrollTo("
                "(document.scrollingElement || document.documentElement).scrollWidth, "
                "window.scrollY);"
            )
        else:
            delta_x = (
                -amount
                if normalized_direction == "left"
                else amount
                if normalized_direction == "right"
                else 0
            )
            delta_y = (
                -amount
                if normalized_direction == "up"
                else amount
                if normalized_direction == "down"
                else 0
            )
            tab.run_js("window.scrollBy(arguments[0], arguments[1]);", delta_x, delta_y)

    def click(self, element) -> None:
        offset = None
        try:
            offset = element.run_js(PREFERRED_CLICK_OFFSET_SCRIPT)
        except Exception:
            offset = None
        if isinstance(offset, dict) and offset.get("offset_x") is not None and offset.get("offset_y") is not None:
            try:
                element.click.at(offset_x=offset["offset_x"], offset_y=offset["offset_y"])
                return
            except Exception:
                pass
        element.click()

    def type_text(self, element, text: str, submit: bool = False) -> None:
        element.input(f"{text}\n" if submit else text, clear=True)

    def _serialize_elements(self, elements, require_element_type: bool = True) -> list[SnapshotNodeRecord]:
        records: OrderedDict[str, SnapshotNodeRecord] = OrderedDict()
        for element in elements:
            payload = element.run_js(SNAPSHOT_SCRIPT, 0)
            for item in payload if isinstance(payload, list) else [payload]:
                if not item or not item.get("xpath"):
                    continue
                if require_element_type and item.get("ref_type") != "element":
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



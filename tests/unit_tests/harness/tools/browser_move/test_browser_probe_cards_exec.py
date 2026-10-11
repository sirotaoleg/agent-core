# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Execute the generated card-probe JavaScript under Node against a small DOM double."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

import pytest

from openjiuwen.harness.tools.browser_move.runtime.probes import build_card_probe_js

_DOM_DOUBLE_JS = r"""
// Minimal DOM double: only what build_card_probe_js() touches on this path.
const DEFAULT_STYLE = { display: 'block', visibility: 'visible', opacity: '1', pointerEvents: 'auto' };
const ATTR = /^\[([\w-]+)(?:([*^$]?=)"([^"]*)"(\s+i)?)?\]/;

const matchesCompound = (el, compound) => {
  let rest = compound.trim();
  if (!rest || /[\s>+~:]/.test(rest.replace(/"[^"]*"/g, '""').replace(/\s+i\]/g, ']'))) return false;
  const tag = rest.match(/^[a-z][a-z0-9]*|^\*/i);
  if (tag) {
    if (tag[0] !== '*' && tag[0].toLowerCase() !== el.localName) return false;
    rest = rest.slice(tag[0].length);
  }
  while (rest) {
    const cls = rest.match(/^\.([\w-]+)/);
    if (cls) {
      if (!el.classNames().includes(cls[1])) return false;
      rest = rest.slice(cls[0].length);
      continue;
    }
    const attr = rest.match(ATTR);
    if (!attr) return false;
    const [, name, op, wanted = '', flag] = attr;
    const actual = el.getAttribute(name);
    if (actual === null) return false;
    const [a, w] = flag ? [actual.toLowerCase(), wanted.toLowerCase()] : [actual, wanted];
    if (op === '=' && a !== w) return false;
    if (op === '*=' && !a.includes(w)) return false;
    if (op === '^=' && !a.startsWith(w)) return false;
    if (op === '$=' && !a.endsWith(w)) return false;
    rest = rest.slice(attr[0].length);
  }
  return true;
};

const matchesSelector = (el, selector) => selector.split(',').some((part) => matchesCompound(el, part));

class FakeElement {
  constructor(tag, attrs = {}, children = [], { text = '', rect = null, style = {} } = {}) {
    this.localName = tag;
    this.tagName = tag.toUpperCase();
    this.nodeType = 1;
    this.attrs = { ...attrs };
    this.children = [];
    this.parentElement = null;
    this.ownText = text;
    this.rect = rect;
    this.style = { ...DEFAULT_STYLE, ...style };
    this.disabled = false;
    children.forEach((child) => this.append(child));
  }
  append(child) { child.parentElement = this; this.children.push(child); }
  get isConnected() { return true; }
  get href() { return this.attrs.href ? new URL(this.attrs.href, 'https://shop.example.test/').href : ''; }
  get previousElementSibling() {
    const siblings = this.parentElement ? this.parentElement.children : [];
    return siblings[siblings.indexOf(this) - 1] || null;
  }
  get textContent() {
    return [this.ownText, ...this.children.map((child) => child.textContent)].filter(Boolean).join(' ');
  }
  get innerText() { return this.textContent; }
  classNames() { return (this.attrs.class || '').split(/\s+/).filter(Boolean); }
  getAttribute(name) { return Object.hasOwn(this.attrs, name) ? String(this.attrs[name]) : null; }
  hasAttribute(name) { return this.getAttribute(name) !== null; }
  getBoundingClientRect() {
    const { left = 0, top = 0, width = 0, height = 0 } = this.rect || {};
    return { left, top, width, height, x: left, y: top, right: left + width, bottom: top + height };
  }
  descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
  querySelectorAll(selector) { return this.descendants().filter((el) => matchesSelector(el, selector)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  matches(selector) { return matchesSelector(this, selector); }
  closest(selector) {
    for (let el = this; el; el = el.parentElement) if (el.matches(selector)) return el;
    return null;
  }
  contains(other) { return other === this || this.descendants().includes(other); }
  remove() {
    if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1);
  }
  cloneNode() {
    const opts = { text: this.ownText, rect: this.rect, style: this.style };
    return new FakeElement(this.localName, this.attrs, this.children.map((child) => child.cloneNode()), opts);
  }
}

const h = (tag, attrs, children, opts) => new FakeElement(tag, attrs, children, opts);

const installDom = (body) => {
  const html = h('html', {}, [body]);
  globalThis.document = {
    title: 'Fake results',
    body,
    documentElement: html,
    querySelectorAll: (selector) => [html, ...html.descendants()].filter((el) => el.matches(selector)),
    querySelector: (selector) => globalThis.document.querySelectorAll(selector)[0] || null,
    elementFromPoint: () => null,
  };
  globalThis.Node = { ELEMENT_NODE: 1 };
  globalThis.window = {
    innerWidth: 1280,
    innerHeight: 800,
    scrollX: 0,
    scrollY: 0,
    location: new URL('https://shop.example.test/search?q=flights'),
    getComputedStyle: (el) => el.style,
  };
};
"""

# Three repeated result cards plus one page-level sort tab whose rendering differs per case.
_FIXTURE_JS = r"""
const sortTab = (style, rect) => h('div', { role: 'tab', 'aria-selected': 'true', class: 'sort-tab' }, [], {
  text: 'Lowest price', rect, style,
});
const card = (index) => h('li', { class: 'result-item' }, [
  h('a', { href: `/item/${index}`, class: 'result-title' }, [], {
    text: `Nonstop flight ${index} Warsaw to Paris`,
    rect: { left: 20, top: 100 + index * 120, width: 400, height: 24 },
  }),
  h('span', { class: 'price' }, [], {
    text: `$${100 + index}`,
    rect: { left: 20, top: 130 + index * 120, width: 80, height: 20 },
  }),
], { rect: { left: 10, top: 90 + index * 120, width: 800, height: 100 } });
const page = (tab) => h('body', {}, [
  h('div', { class: 'sort-bar' }, [tab], { rect: { left: 0, top: 10, width: 1280, height: 40 } }),
  h('ul', { class: 'result-list' }, [card(1), card(2), card(3)], {
    rect: { left: 0, top: 100, width: 1280, height: 600 },
  }),
], { rect: { left: 0, top: 0, width: 1280, height: 2000 } });

const cases = {
  rendered_tab: sortTab({}, { left: 10, top: 10, width: 120, height: 32 }),
  hidden_tab: sortTab({ display: 'none' }, { left: 10, top: 10, width: 120, height: 32 }),
  off_viewport_tab: sortTab({}, { left: 10, top: -500, width: 120, height: 32 }),
};

(async () => {
  const results = {};
  for (const [name, tab] of Object.entries(cases)) {
    installDom(page(tab));
    try {
      const result = await probe({ evaluate: async (fn, arg) => fn(arg) });
      results[name] = {
        ok: result.ok,
        cards: result.cards.map((item) => ({ title: item.title, sort_state: item.sort_state })),
      };
    } catch (error) {
      results[name] = { error: String(error) };
    }
  }
  console.log(JSON.stringify(results));
})();
"""

FLIGHT_TITLES = {f"Nonstop flight {index} Warsaw to Paris" for index in (1, 2, 3)}


@pytest.fixture(scope="module")
def probe_results(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; skipping generated JavaScript execution test")

    runner = tmp_path_factory.mktemp("card_probe") / "run_card_probe.js"
    runner.write_text(
        f"const probe = ({build_card_probe_js()});\n{_DOM_DOUBLE_JS}\n{_FIXTURE_JS}",
        encoding="utf-8",
    )
    completed = subprocess.run([node, str(runner)], check=True, text=True, capture_output=True)
    return json.loads(completed.stdout)


def _flight_cards(result: dict[str, Any]) -> list[dict[str, Any]]:
    assert "error" not in result, f"card probe threw: {result.get('error')}"
    assert result["ok"] is True
    cards = [card for card in result["cards"] if card["title"] in FLIGHT_TITLES]
    assert {card["title"] for card in cards} == FLIGHT_TITLES
    return cards


def test_card_probe_reports_active_sort_tab(probe_results: dict[str, Any]) -> None:
    cards = _flight_cards(probe_results["rendered_tab"])

    assert [card["sort_state"] for card in cards] == ["Lowest price"] * 3


def test_card_probe_ignores_hidden_sort_tab(probe_results: dict[str, Any]) -> None:
    cards = _flight_cards(probe_results["hidden_tab"])

    assert all(card["sort_state"] != "Lowest price" for card in cards)


def test_card_probe_reports_sort_tab_scrolled_out_of_view(probe_results: dict[str, Any]) -> None:
    cards = _flight_cards(probe_results["off_viewport_tab"])

    assert [card["sort_state"] for card in cards] == ["Lowest price"] * 3

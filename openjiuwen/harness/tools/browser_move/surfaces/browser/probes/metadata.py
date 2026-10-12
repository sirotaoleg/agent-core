# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The page script that reads the page, tab and scroll position state."""

from __future__ import annotations


def build_browser_state_metadata_js() -> str:
    """Build Playwright code for fresh page, tab, and position state."""

    return r"""
async (page) => {
  const pages = page.context().pages();

  const tabs = await Promise.all(
    pages.map(async (tab, index) => {
      let title = '';
      try {
        title = await tab.title();
      } catch (_error) {
        title = '';
      }
      return {
        index,
        current: tab === page,
        url: tab.url(),
        title,
      };
    })
  );

  const pageMetadata = await page.evaluate(() => {
    const root = document.documentElement;
    const body = document.body;
    const viewportWidth = Math.max(0, window.innerWidth || root?.clientWidth || 0);
    const viewportHeight = Math.max(0, window.innerHeight || root?.clientHeight || 0);
    const pageWidth = Math.max(
      viewportWidth,
      root?.scrollWidth || 0,
      body?.scrollWidth || 0
    );
    const pageHeight = Math.max(
      viewportHeight,
      root?.scrollHeight || 0,
      body?.scrollHeight || 0
    );
    const scrollX = Math.max(0, window.scrollX || window.pageXOffset || 0);
    const scrollY = Math.max(0, window.scrollY || window.pageYOffset || 0);

    const pagePosition = {
      viewport_width: viewportWidth,
      viewport_height: viewportHeight,
      page_width: pageWidth,
      page_height: pageHeight,
      scroll_x: scrollX,
      scroll_y: scrollY,
      pixels_above: scrollY,
      pixels_below: Math.max(0, pageHeight - scrollY - viewportHeight),
      pixels_left: scrollX,
      pixels_right: Math.max(0, pageWidth - scrollX - viewportWidth),
    };

    const compact = (value, limit = 160) => String(value || '')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, limit);
    const visible = (element) => {
      if (!element || !element.isConnected) return false;
      const rect = element.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return false;
      const style = window.getComputedStyle(element);
      return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) !== 0;
    };
    const elementKey = (element, index) => compact(
      element.getAttribute('name') ||
      element.getAttribute('id') ||
      element.getAttribute('aria-label') ||
      element.getAttribute('placeholder') ||
      compact(element.innerText || element.textContent || '', 80) ||
      `${element.tagName.toLowerCase()}:${element.getAttribute('type') || index}`,
      120
    ).toLowerCase();

    const formValues = [];
    const formElements = Array.from(document.querySelectorAll(
      'input:not([type="password"]):not([type="hidden"]),select,textarea,[contenteditable="true"]'
    )).slice(0, 80);
    formElements.forEach((element, index) => {
      if (!visible(element) || element.disabled) return;
      const type = String(element.getAttribute('type') || '').toLowerCase();
      let value = '';
      if (type === 'checkbox' || type === 'radio') {
        if (!element.checked) return;
        value = element.value || true;
      } else if (element.tagName.toLowerCase() === 'select') {
        value = Array.from(element.selectedOptions || []).map((option) => compact(option.text || option.value));
      } else if (element.isContentEditable) {
        value = compact(element.innerText || element.textContent || '');
      } else {
        value = compact(element.value || '');
      }
      if (value === '' || (Array.isArray(value) && value.length === 0)) return;
      formValues.push({ key: elementKey(element, index), value });
    });

    const selectedFilters = [];
    const selectedCandidates = Array.from(document.querySelectorAll(
      'input:checked,select,[aria-selected="true"],[aria-checked="true"],' +
      '[aria-current="true"],[data-state="active"],[data-state="selected"],' +
      '[role="tab"].active,[role="tab"].selected,' +
      '[class*="filter" i] .selected,[class*="filter" i] .active,' +
      '[class*="facet" i] .selected,[class*="facet" i] .active,' +
      '[class*="sort" i] .selected,[class*="sort" i] .active,' +
      '[class*="price" i] .selected,[class*="price" i] .active,' +
      '[class*="rating" i] .selected,[class*="rating" i] .active'
    )).slice(0, 120);
    selectedCandidates.forEach((element, index) => {
      if (!visible(element)) return;
      const descriptor = compact([
        element.getAttribute('id') || '',
        element.getAttribute('class') || '',
        element.getAttribute('role') || '',
        element.getAttribute('aria-label') || '',
        element.getAttribute('data-state') || '',
        element.innerText || element.textContent || '',
        element.parentElement?.getAttribute('class') || '',
      ].join(' '), 300).toLowerCase();
      const nativeSelected = element.matches(
        'input:checked,select,[aria-checked="true"],[aria-selected="true"],[aria-current="true"]'
      );
      const filterContext = /(filter|facet|sort|order|price|rating|score|star|category|筛选|排序|价格|评分|星级|分类|销量|综合|最新)/.test(descriptor);
      if (!nativeSelected && !filterContext) return;
      let value = compact(
        element.getAttribute('aria-label') || element.innerText || element.textContent || element.value || ''
      );
      if (element.tagName.toLowerCase() === 'select') {
        value = Array.from(element.selectedOptions || []).map((option) => compact(option.text || option.value)).join('|');
      }
      if (!value) return;
      const selectionSource =
        element.getAttribute('aria-selected') === 'true' ? 'aria-selected' :
        element.getAttribute('aria-checked') === 'true' ? 'aria-checked' :
        element.getAttribute('aria-current') === 'true' ? 'aria-current' :
        ['active', 'selected'].includes(String(element.getAttribute('data-state') || '').toLowerCase())
          ? 'data-state' :
          /(?:^|\s)(?:active|selected)(?:\s|$)/i.test(String(element.getAttribute('class') || ''))
            ? 'class' :
            element.matches('input:checked,select') ? 'native' : 'semantic-context';
      selectedFilters.push({ key: elementKey(element, index), value, source: selectionSource });
    });

    let resultCount = null;
    const countCandidates = Array.from(document.querySelectorAll(
      '[data-total],[data-count],[aria-rowcount],[class*="result-count" i],[class*="total-count" i]'
    )).slice(0, 40);
    for (const element of countCandidates) {
      const raw = element.getAttribute('data-total') || element.getAttribute('data-count') ||
        element.getAttribute('aria-rowcount') || compact(element.textContent || '', 120);
      const match = String(raw || '').replace(/,/g, '').match(/\d+/);
      if (!match) continue;
      resultCount = Number(match[0]);
      break;
    }
    const resultCandidates = new Set();
    for (const selector of [
      'main article', 'main [role="article"]', 'main [role="row"]',
      '[data-testid*="result" i]', '[data-test*="result" i]',
      '[class*="search-result" i]', '[class*="result-item" i]'
    ]) {
      document.querySelectorAll(selector).forEach((element) => {
        if (visible(element)) resultCandidates.add(element);
      });
    }
    if (resultCount === null && resultCandidates.size > 0) resultCount = resultCandidates.size;
    const firstResult = Array.from(resultCandidates)[0];
    const firstResultText = firstResult ? compact(firstResult.innerText || firstResult.textContent || '', 240) : '';

    const actionFeedback = [];
    const feedbackCandidates = Array.from(document.querySelectorAll(
      '[role="status"],[role="alert"],[aria-live]:not([aria-live="off"]),[class*="toast" i]'
    )).slice(0, 40);
    feedbackCandidates.forEach((element, index) => {
      if (!visible(element)) return;
      const value = compact(element.innerText || element.textContent || element.getAttribute('aria-label') || '', 240);
      if (!value || !/(added|cart|basket|success|completed|已加入|购物车|成功|完成)/i.test(value)) return;
      actionFeedback.push({ key: `feedback:${index}`, value });
    });

    const commerceState = [];
    const commerceCandidates = Array.from(document.querySelectorAll(
      '[aria-label*="cart" i],[aria-label*="basket" i],[title*="cart" i],[title*="购物车" i],' +
      '[class*="cart-count" i],[class*="cart-num" i],[data-testid*="cart" i]'
    )).slice(0, 40);
    commerceCandidates.forEach((element, index) => {
      if (!visible(element)) return;
      const value = compact(
        element.getAttribute('aria-label') || element.getAttribute('title') || element.innerText || element.textContent || '',
        160
      );
      if (!value || !/\d/.test(value)) return;
      commerceState.push({ key: elementKey(element, index), value });
    });

    return {
      page_position: pagePosition,
      semantic_state: {
        form_values: formValues.slice(0, 32),
        selected_filters: selectedFilters.slice(0, 32),
        result_count: resultCount,
        first_result_text: firstResultText,
        action_feedback: actionFeedback.slice(0, 4),
        commerce_state: commerceState.slice(0, 8),
      },
    };
  });

  let title = '';
  try {
    title = await page.title();
  } catch (_error) {
    title = '';
  }

  return {
    ok: true,
    url: page.url(),
    title,
    tabs,
    page_position: pageMetadata.page_position,
    semantic_state: pageMetadata.semantic_state,
  };
}
""".strip()

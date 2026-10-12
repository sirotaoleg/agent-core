# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The page script that lists the interactive elements."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .cards import _clamp_int


def build_interactive_probe_js(
    *,
    max_items: int = 50,
    viewport_only: bool = True,
    query: str = "",
    site_profiles: Optional[List[Dict[str, Any]]] = None,
    generation_id: str = "g0",
) -> str:
    """Build the JavaScript for compact interactive-element probing."""

    params: Dict[str, Any] = {
        "max_items": _clamp_int(max_items, default=50, minimum=1, maximum=100),
        "viewport_only": bool(viewport_only),
        "query": str(query or "").strip().lower(),
        "site_profiles": site_profiles or [],
        "generation_id": str(generation_id or "g0"),
    }
    params_json = json.dumps(params, ensure_ascii=False)

    return f"""
async (page) => {{
  const params = {params_json};

  return await page.evaluate((params) => {{
    const maxItems = Math.max(1, Math.min(Number(params.max_items || 50), 100));
    const viewportOnly = params.viewport_only !== false;
    const query = String(params.query || '').trim().toLowerCase();
    const generationId = String(params.generation_id || 'g0');
    const host = String(window.location.hostname || '').toLowerCase();
    const activeControlSemantics = Array.isArray(params.site_profiles)
      ? params.site_profiles
          .filter((profile) => (profile.domains || []).some((domain) => {{
            const value = String(domain || '').toLowerCase();
            return host === value || host.endsWith(`.${{value}}`);
          }}))
          .map((profile) => profile.semantic_rules?.control_semantics)
          .filter(Boolean)
      : [];

    const matchesProfilePattern = (value, patterns) => {{
      return Array.isArray(patterns) && patterns.some((pattern) => {{
        try {{
          return new RegExp(String(pattern), 'i').test(String(value || ''));
        }} catch (_) {{
          return false;
        }}
      }});
    }};

    const selectors = [
      'button',
      'a[href]',
      'input',
      'select',
      'textarea',
      '[role="button"]',
      '[role="link"]',
      '[role="textbox"]',
      '[role="searchbox"]',
      '[role="checkbox"]',
      '[role="radio"]',
      '[role="tab"]',
      '[role="option"]',
      '[role="gridcell"]',
      '[role="menuitem"]',
      '[aria-selected]',
      '[aria-checked]',
      '[aria-sort]',
      '[data-date]',
      '[data-day]',
      '[class*="calendar" i] [class*="day" i]',
      '[class*="calendar" i] [class*="date" i]',
      '[class*="datepicker" i] [class*="day" i]',
      '[class*="date-picker" i] [class*="day" i]',
      '[class*="sort" i] [class*="item" i]',
      '[class*="sort" i] [class*="option" i]',
      '[class*="rating" i] [class*="item" i]',
      '[class*="rating" i] [class*="option" i]',
      '[contenteditable="true"]',
      '[aria-label]',
      '[placeholder]',
      '[name]',
      '[data-testid]',
      '[data-test]',
      '[data-cy]'
    ];

    const normalize = (value, limit = 140) => {{
      return String(value || '')
        .replace(/\\s+/g, ' ')
        .trim()
        .slice(0, limit);
    }};

    const attrEscape = (value) => {{
      return String(value || '').replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
    }};

    const cssEscape = (value) => {{
      const raw = String(value || '');
      if (window.CSS && typeof window.CSS.escape === 'function') {{
        return window.CSS.escape(raw);
      }}
      return raw.replace(/[^a-zA-Z0-9_-]/g, '\\\\$&');
    }};

    const roleFromTag = (el) => {{
      const explicit = el.getAttribute('role');
      if (explicit) return explicit;

      const tag = el.tagName.toLowerCase();
      const type = String(el.getAttribute('type') || '').toLowerCase();

      if (tag === 'button') return 'button';
      if (tag === 'a') return 'link';
      if (el.getAttribute('contenteditable') === 'true') return 'textbox';
      if (tag === 'select') return 'combobox';
      if (tag === 'textarea') return 'textbox';
      if (tag === 'input') {{
        if (type === 'checkbox') return 'checkbox';
        if (type === 'radio') return 'radio';
        if (type === 'submit' || type === 'button') return 'button';
        return 'textbox';
      }}
      return '';
    }};

    const isVisible = (el, rect) => {{
      if (!el || !rect) return false;
      if (rect.width < 2 || rect.height < 2) return false;

      const style = window.getComputedStyle(el);
      if (!style) return false;
      if (style.display === 'none') return false;
      if (style.visibility === 'hidden') return false;
      if (Number(style.opacity) === 0) return false;

      if (viewportOnly) {{
        if (rect.bottom < 0) return false;
        if (rect.right < 0) return false;
        if (rect.top > window.innerHeight) return false;
        if (rect.left > window.innerWidth) return false;
      }}

      return true;
    }};

    const validateSelectorHint = (el, selector) => {{
      if (!el || !selector) return '';
      try {{
        const matches = Array.from(document.querySelectorAll(selector));
        return matches.length === 1 && matches[0] === el ? selector : '';
      }} catch (_) {{
        return '';
      }}
    }};

    const buildSelectorHint = (el) => {{
      const tag = el.tagName.toLowerCase();

      const testid =
        el.getAttribute('data-testid') ||
        el.getAttribute('data-test') ||
        el.getAttribute('data-cy');
      if (testid) {{
        let candidate = `[data-cy="${{attrEscape(testid)}}"]`;
        if (el.getAttribute('data-testid')) candidate = `[data-testid="${{attrEscape(testid)}}"]`;
        if (el.getAttribute('data-test')) candidate = `[data-test="${{attrEscape(testid)}}"]`;
        const validated = validateSelectorHint(el, candidate);
        if (validated) return validated;
      }}

      const id = el.getAttribute('id');
      if (id) {{
        const validated = validateSelectorHint(el, `#${{cssEscape(id)}}`);
        if (validated) return validated;
      }}

      const aria = el.getAttribute('aria-label');
      if (aria) {{
        const validated = validateSelectorHint(
          el,
          `${{tag}}[aria-label="${{attrEscape(aria)}}"]`
        );
        if (validated) return validated;
      }}

      const name = el.getAttribute('name');
      if (name) {{
        const validated = validateSelectorHint(el, `${{tag}}[name="${{attrEscape(name)}}"]`);
        if (validated) return validated;
      }}

      const placeholder = el.getAttribute('placeholder');
      if (placeholder) {{
        const validated = validateSelectorHint(
          el,
          `${{tag}}[placeholder="${{attrEscape(placeholder)}}"]`
        );
        if (validated) return validated;
      }}

      const path = [];
      let node = el;
      let depth = 0;

      while (node && node.nodeType === Node.ELEMENT_NODE && depth < 4) {{
        const nodeTag = node.tagName.toLowerCase();
        let index = 1;
        let prev = node.previousElementSibling;
        while (prev) {{
          if (prev.tagName.toLowerCase() === nodeTag) index += 1;
          prev = prev.previousElementSibling;
        }}
        path.unshift(`${{nodeTag}}:nth-of-type(${{index}})`);
        node = node.parentElement;
        depth += 1;
      }}

      return validateSelectorHint(el, path.join(' > '));
    }};

    const isActionable = (el, rect) => {{
      if (!el || !rect) return false;
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
      if (el.hasAttribute('inert')) return false;
      const style = window.getComputedStyle(el);
      if (!style || style.pointerEvents === 'none') return false;

      const x = Math.max(0, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
      const y = Math.max(0, Math.min(window.innerHeight - 1, rect.top + rect.height / 2));
      const hit = document.elementFromPoint(x, y);
      return !hit || hit === el || el.contains(hit) || hit.contains(el);
    }};

    const elementText = (el) => {{
      const tag = el.tagName.toLowerCase();
      if (tag === 'input' || tag === 'textarea') {{
        return normalize(el.value || el.getAttribute('value') || '');
      }}
      return normalize(el.innerText || el.textContent || '');
    }};

    const accessibleName = (el) => {{
      return normalize(
        el.getAttribute('aria-label') ||
        el.getAttribute('title') ||
        el.getAttribute('placeholder') ||
        el.getAttribute('alt') ||
        el.getAttribute('name') ||
        ''
      );
    }};

    const semanticContext = (el) => {{
      const parts = [];
      let node = el;
      let depth = 0;
      while (node && node.nodeType === Node.ELEMENT_NODE && depth < 5) {{
        parts.push(
          node.tagName || '',
          node.getAttribute('id') || '',
          node.getAttribute('class') || '',
          node.getAttribute('role') || '',
          node.getAttribute('aria-label') || '',
          node.getAttribute('data-testid') || '',
          node.getAttribute('data-date') || '',
          node.getAttribute('data-day') || ''
        );
        node = node.parentElement;
        depth += 1;
      }}
      return normalize(parts.join(' '), 700).toLowerCase();
    }};

    const classifyRegion = (el, searchable) => {{
      const semantic = semanticContext(el);
      const searchableText = String(searchable || '').toLowerCase();
      const inPageChrome = Boolean(el.closest && el.closest('header,nav,[class*="header" i]'));
      for (const profile of activeControlSemantics) {{
        const matchesContext = matchesProfilePattern(
          `${{semantic}} ${{searchableText}}`,
          profile.context_patterns
        );
        if (matchesContext && !inPageChrome) return String(profile.region || 'main');
        if (inPageChrome && matchesProfilePattern(searchableText, profile.global_search_patterns)) {{
          return 'global_search';
        }}
      }}
      if (el.closest && el.closest('aside,[role="complementary"]')) return 'sidebar';
      const own = normalize([
        el.tagName || '', el.getAttribute('id') || '', el.getAttribute('class') || '',
        el.getAttribute('role') || '', el.getAttribute('aria-label') || '',
        el.getAttribute('data-testid') || ''
      ].join(' '), 360).toLowerCase();
      const context = `${{own}} ${{String(searchable || '').toLowerCase()}}`;
      if (/(sidebar|side-bar|aside|right-rail|right-panel|complementary)/.test(context)) return 'sidebar';
      if (/(hot[-_ ]?(search|list|rank|topic)|hotlist|hotrank|toplist|trending)/.test(context) || /\u70ed\u641c|\u70ed\u699c/.test(context)) return 'hot_search';
      if (/(account|profile|user-card|creator-card)/.test(context)) return 'account';
      if (/(shop|store|seller)/.test(context) || /\u5e97\u94fa|\u5356\u5bb6/.test(context)) return 'shop';
      if (/(chat|message|wangwang|aliim|contact-seller)/.test(context) || /\u65fa\u65fa|\u8054\u7cfb\u5356\u5bb6/.test(context)) return 'chat';
      return 'main';
    }};

    const classifyControlKind = (el, text, name) => {{
      const role = String(el.getAttribute('role') || '').toLowerCase();
      const tag = el.tagName.toLowerCase();
      const type = String(el.getAttribute('type') || '').toLowerCase();
      const own = normalize([
        el.getAttribute('id') || '', el.getAttribute('class') || '', role,
        el.getAttribute('aria-label') || '', el.getAttribute('data-testid') || '',
        el.getAttribute('data-date') || '', el.getAttribute('data-day') || '',
        el.getAttribute('name') || '', el.getAttribute('placeholder') || '',
        text || '', name || ''
      ].join(' '), 520).toLowerCase();
      const ancestor = semanticContext(el);
      for (const profile of activeControlSemantics) {{
        if (!matchesProfilePattern(`${{ancestor}} ${{own}}`, profile.context_patterns)) continue;
        for (const candidate of profile.kinds || []) {{
          if (!matchesProfilePattern(own, candidate.patterns)) continue;
          const shapeRestrictions = [candidate.tags, candidate.roles, candidate.types]
            .some((values) => Array.isArray(values) && values.length);
          const shapeMatches = !shapeRestrictions ||
            (candidate.tags || []).includes(tag) ||
            (candidate.roles || []).includes(role) ||
            (candidate.types || []).includes(type);
          if (shapeMatches) return String(candidate.kind || '');
        }}
      }}
      if (
        ['input', 'textarea', 'select'].includes(tag) ||
        el.isContentEditable ||
        ['textbox', 'searchbox', 'combobox'].includes(role) ||
        ['text', 'search', 'email', 'number', 'password'].includes(type)
      ) return '';
      const calendarAncestor = /(calendar|date[-_ ]?picker|datepicker)/.test(ancestor) || /\u65e5\u5386|\u9009\u62e9\u65e5\u671f/.test(ancestor);
      const directDateEvidence = role === 'gridcell' || el.hasAttribute('data-date') ||
        el.hasAttribute('data-day') || /(date-cell|day-cell|calendar-day)/.test(own);
      if (directDateEvidence && calendarAncestor && /[0-9]/.test(own)) {{
        return 'calendar_date';
      }}
      const sortAncestor = /(sort|order|sort-list|sort-tabs)/.test(ancestor) || /\u6392\u5e8f/.test(ancestor);
      const sortLabel = /(?:^|\s)(sales?|volume|price|latest|newest|relevance|comprehensive)(?:\s|$)/.test(own) ||
        /(\u9500\u91cf|\u4ef7\u683c|\u6700\u65b0|\u7efc\u5408|\u8bc4\u5206)/.test(own);
      if ((role === 'tab' || /(sort-item|sort-option|sort-tab)/.test(own)) && (sortAncestor || sortLabel)) return 'sort_tab';
      const ratingAncestor = /(rating|score|star|rating-filter)/.test(ancestor) || /\u8bc4\u5206|\u661f\u7ea7/.test(ancestor);
      if ((role === 'option' || /(rating-item|rating-option|star-item)/.test(own)) && ratingAncestor) return 'rating_filter';
      if (role === 'tab') return 'tab';
      if (role === 'option') return 'option';
      return '';
    }};

    const selectionSourceFromUrl = (el, kind) => {{
      if (!['sort', 'sort_tab', 'sort_option', 'rating_filter'].includes(String(kind || ''))) return '';
      let current;
      try {{
        current = new URL(window.location.href);
      }} catch (_error) {{
        return '';
      }}
      const anchor = el.closest && el.closest('a[href]');
      if (anchor) {{
        try {{
          const candidate = new URL(anchor.href, current.href);
          if (candidate.origin === current.origin && candidate.pathname === current.pathname) {{
            const candidateEntries = [...candidate.searchParams.entries()];
            if (
              candidateEntries.length > 0 &&
              candidateEntries.every(([key, value]) => current.searchParams.get(key) === value)
            ) return 'url';
          }}
        }} catch (_error) {{
          // Fall through to data-value matching.
        }}
      }}
      const candidateValues = [
        el.getAttribute('data-value'),
        el.getAttribute('data-sort'),
        el.getAttribute('data-order'),
        el.getAttribute('value')
      ].filter(Boolean).map((value) => String(value).trim().toLowerCase());
      const currentValues = [...current.searchParams.values()]
        .map((value) => String(value).trim().toLowerCase());
      return candidateValues.some((value) => currentValues.includes(value)) ? 'url-param' : '';
    }};

    const queryAliases = (value) => {{
      const raw = String(value || '').trim().toLowerCase();
      if (!raw) return [];

      const aliases = new Set([raw]);
      const add = (items) => items.forEach((item) => aliases.add(item));

      if (['search', 'find', 'query', 'keyword', 'keywords',
        '搜索', '搜尋', '查询', '查找', '关键词', '关键字', '搜寻', '検索']
        .some((term) => raw.includes(term))) {{
        add(['search', 'find', 'query', 'keyword', 'keywords', 'searchbox', 'search-box',
          'search_input', 'search-input',
          '搜索', '搜', '搜尋', '查询', '查找', '关键词', '关键字', '搜寻', '検索']);
      }}

      if (['input', 'textbox', 'text box', 'field', '输入', '輸入', '输入框', '文字框']
        .some((term) => raw.includes(term))) {{
        add(['input', 'textbox', 'text box', 'field', 'textarea', 'keyword',
          'search', 'query', '输入', '輸入', '搜索', '搜尋', '关键词', '关键字']);
      }}

      if (['next', 'pagination', 'page', '下一页', '下一頁', '下页', '下頁', '更多']
        .some((term) => raw.includes(term))) {{
        add(['next', 'pagination', 'page', '下一页', '下一頁', '下页', '下頁',
          '更多', '加载更多', '載入更多', 'load more']);
      }}

      if (['login', 'sign in', 'signin', '登录', '登入', '登陆']
        .some((term) => raw.includes(term))) {{
        add(['login', 'sign in', 'signin', 'log in', '登录', '登入', '登陆']);
      }}

      return Array.from(aliases).filter(Boolean);
    }};

    const queryTerms = queryAliases(query);
    const exactQuery = String(query || '').trim().toLowerCase();

    const classifyActionLikelihood = (el, searchable, kind = '') => {{
      const tag = el.tagName.toLowerCase();
      const type = String(el.getAttribute('type') || '').toLowerCase();
      const role = roleFromTag(el);
      const text = String(searchable || '').toLowerCase();

      if (kind === 'calendar_date') return 'date';
      if (kind === 'sort_tab') return 'sort';
      if (kind === 'rating_filter') return 'rating';
      if (['hotel_checkin', 'hotel_checkout'].includes(kind)) return 'date';
      if (kind === 'hotel_destination') return 'input';
      if (kind === 'hotel_search_submit') return 'search';
      if (kind === 'hotel_filter') return 'filter';

      if (
        type === 'search' ||
        role === 'searchbox' ||
        /\b(search|query|keyword|kw|wd)\b/i.test(text) ||
        /(搜索|搜尋|查询|查找|关键词|关键字|検索)/.test(text)
      ) {{
        return 'search';
      }}

      if (['input', 'textarea'].includes(tag) || role === 'textbox') return 'input';
      if (/\b(next|pagination|page)\b/i.test(text) || /(下一页|下一頁|下页|下頁|更多|加载更多|載入更多)/.test(text)) return 'pagination';
      if (/\b(login|sign in|signin|log in)\b/i.test(text) || /(登录|登入|登陆)/.test(text)) return 'login';
      if (/\b(filter|sort|category)\b/i.test(text) || /(筛选|篩選|排序|分类|分類)/.test(text)) return 'filter';
      if (/\b(cart|basket|buy|checkout)\b/i.test(text) || /(购物车|購物車|加入购物车|加入購物車|购买|購買)/.test(text)) return 'commerce';

      if (tag === 'button') return 'button';
      if (tag === 'a') return 'link';
      return role || tag;
    }};

    const queryMatches = (searchable) => {{
      if (!queryTerms.length) return true;
      const haystack = String(searchable || '').toLowerCase();
      return queryTerms.some((term) => haystack.includes(term));
    }};

    const exactQueryMatches = (searchable) => {{
      if (!exactQuery) return true;
      return String(searchable || '').toLowerCase().includes(exactQuery);
    }};

    const scoreElement = (el, rect, text, name, actionLikelihood) => {{
      let score = 0;
      const tag = el.tagName.toLowerCase();
      const role = roleFromTag(el);

      if (el.getAttribute('data-testid')) score += 40;
      if (el.getAttribute('data-test') || el.getAttribute('data-cy')) score += 30;
      if (el.getAttribute('aria-label')) score += 25;
      if (tag === 'button') score += 25;
      if (tag === 'input' || tag === 'select' || tag === 'textarea') score += 22;
      if (el.getAttribute('contenteditable') === 'true') score += 18;
      if (tag === 'a') score += 18;
      if (role) score += 15;
      if (actionLikelihood === 'search') score += 35;
      if (['date', 'sort', 'rating'].includes(actionLikelihood)) score += 30;
      if (query && queryMatches(`${{actionLikelihood}} ${{tag}} ${{role}}`)) score += 20;
      if (text) score += Math.min(20, text.length / 4);
      if (name) score += Math.min(15, name.length / 5);

      if (rect.top >= 0 && rect.top <= window.innerHeight) score += 15;
      if (rect.left >= 0 && rect.left <= window.innerWidth) score += 5;

      if (el.disabled || el.getAttribute('aria-disabled') === 'true') score -= 50;

      return score;
    }};

    const all = Array.from(document.querySelectorAll(selectors.join(',')));
    const seen = new Set();
    const candidates = [];
    const widenedCandidates = [];

    for (const el of all) {{
      if (!el || seen.has(el)) continue;
      seen.add(el);

      const rect = el.getBoundingClientRect();
      if (!isVisible(el, rect)) continue;

      const tag = el.tagName.toLowerCase();
      const role = roleFromTag(el);
      const text = elementText(el);
      const name = accessibleName(el);
      const testid =
        el.getAttribute('data-testid') ||
        el.getAttribute('data-test') ||
        el.getAttribute('data-cy') ||
        '';
      const type = el.getAttribute('type') || '';
      const id = el.getAttribute('id') || '';
      const nameAttr = el.getAttribute('name') || '';
      const placeholder = el.getAttribute('placeholder') || '';
      const className = el.getAttribute('class') || '';
      const title = el.getAttribute('title') || '';

      const searchable = `${{tag}} ${{role}} ${{type}} ${{id}} ${{nameAttr}} ${{className}} ${{text}} ${{name}} ${{placeholder}} ${{title}} ${{testid}}`.toLowerCase();
      const exactMatch = exactQueryMatches(searchable);
      if (!exactMatch && !queryMatches(searchable)) continue;

      const kind = classifyControlKind(el, text, name);
      const actionLikelihood = classifyActionLikelihood(el, searchable, kind);
      const selectorHint = buildSelectorHint(el);
      const actionable = isActionable(el, rect);
      const matchCount = selectorHint
        ? document.querySelectorAll(selectorHint).length
        : 0;
      const enabled = !(
        el.disabled ||
        el.getAttribute('aria-disabled') === 'true' ||
        el.hasAttribute('inert')
      );
      const clickable = Boolean(
        actionable && enabled && selectorHint && matchCount === 1
      );
      const selectedSource =
        el.getAttribute('aria-selected') === 'true' ? 'aria-selected' :
        el.getAttribute('aria-checked') === 'true' ? 'aria-checked' :
        el.getAttribute('aria-current') === 'true' ? 'aria-current' :
        ['active', 'selected', 'checked'].includes(String(el.getAttribute('data-state') || '').toLowerCase()) ? 'data-state' :
        /(^|\\s)(active|selected|checked)(\\s|$)/i.test(className) ? 'class' :
        selectionSourceFromUrl(el, kind);

      const candidate = {{
        tag,
        role,
        action_likelihood: actionLikelihood,
        region: classifyRegion(el, searchable),
        kind: kind || actionLikelihood,
        text,
        accessible_name: name,
        aria_label: normalize(el.getAttribute('aria-label') || ''),
        testid: normalize(testid),
        input_type: normalize(type),
        name: normalize(nameAttr),
        placeholder: normalize(placeholder),
        href: normalize(el.getAttribute('href') || '', 180),
        disabled: Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true'),
        selected: Boolean(selectedSource),
        selected_source: selectedSource,
        visible: true,
        enabled,
        actionable,
        clickable,
        match_count: matchCount,
        generation_id: generationId,
        bbox: [
          Math.round(rect.x),
          Math.round(rect.y),
          Math.round(rect.width),
          Math.round(rect.height)
        ],
        selector_hint: clickable ? selectorHint : '',
        selector_hint_validated: clickable,
        score: scoreElement(el, rect, text, name, actionLikelihood)
      }};
      (exactMatch ? candidates : widenedCandidates).push(candidate);
    }}

    const selectedCandidates = candidates.length ? candidates : widenedCandidates;
    selectedCandidates.sort((a, b) => {{
      if (b.score !== a.score) return b.score - a.score;
      if (a.bbox[1] !== b.bbox[1]) return a.bbox[1] - b.bbox[1];
      return a.bbox[0] - b.bbox[0];
    }});

    const elements = selectedCandidates.slice(0, maxItems).map((item, index) => {{
      const copy = {{ ...item }};
      copy.id = `e${{index + 1}}`;
      delete copy.score;
      return copy;
    }});

    return {{
      ok: true,
      url: window.location.href,
      title: document.title,
      viewport: {{
        width: window.innerWidth,
        height: window.innerHeight,
        scroll_x: window.scrollX,
        scroll_y: window.scrollY
      }},
      query,
      viewport_only: viewportOnly,
      generation_id: generationId,
      total_candidates: selectedCandidates.length,
      query_widened: Boolean(exactQuery && !candidates.length && widenedCandidates.length),
      returned: elements.length,
      elements,
      error: null
    }};
  }}, params);
}}
""".strip()

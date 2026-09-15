/**
 * Modometa client script:
 * - Timeframe select dropdown (30d vs 90d vs 180d vs 365d)
 * - Lightweight Scryfall card preview tooltip on hover
 * - Mobile sidebar drawer navigation toggle
 * - Dark mode theme toggle
 * - Global search autocomplete (archetypes & players)
 * - Horizontal scroll initial alignment (bump chart & heatmap)
 */

document.addEventListener('DOMContentLoaded', () => {
  // Clean up legacy localStorage key if present
  try {
    localStorage.removeItem('modometa_timeframe');
  } catch (e) {
    // Ignore storage exceptions
  }

  // 1. Timeframe select dropdown change handler
  document.querySelectorAll('[data-timeframe-select]').forEach(select => {
    select.addEventListener('change', (e) => {
      const days = e.target.value;
      const url = new URL(window.location.href);
      if (days === '90') {
        url.searchParams.delete('days');
      } else {
        url.searchParams.set('days', days);
      }
      url.searchParams.delete('page');
      window.location.href = url.toString();
    });
  });

  // 2. Scryfall Card Hover Tooltip
  const tooltip = document.getElementById('card-hover-tooltip');
  if (tooltip) {
    let activeImgUri = '';
    let loadRequestId = 0;
    let lastClientX = 0;
    let lastClientY = 0;

    function positionTooltip(clientX, clientY) {
      let x = clientX + 20;
      let y = clientY - 140;

      // Viewport collision bounds
      if (x + 270 > window.innerWidth) {
        x = clientX - 280;
      }
      if (y + 360 > window.innerHeight) {
        y = window.innerHeight - 370;
      }
      if (y < 10) y = 10;

      tooltip.style.left = `${x}px`;
      tooltip.style.top = `${y}px`;
    }

    document.addEventListener('mouseover', (e) => {
      const target = e.target.closest('[data-card-image]');
      if (!target) return;

      const imgUri = target.getAttribute('data-card-image');
      if (!imgUri) return;

      lastClientX = e.clientX;
      lastClientY = e.clientY;
      positionTooltip(lastClientX, lastClientY);

      // If moving to a different card, immediately hide and clear to avoid showing the old card
      if (activeImgUri !== imgUri) {
        tooltip.style.display = 'none';
        tooltip.removeAttribute('src');
      }

      activeImgUri = imgUri;
      const currentReq = ++loadRequestId;

      const preloader = new Image();
      preloader.src = imgUri;

      // If image is already cached in memory, show immediately
      if (preloader.complete && preloader.naturalWidth > 0) {
        tooltip.src = imgUri;
        tooltip.style.display = 'block';
        return;
      }

      // Otherwise wait for network load before displaying tooltip
      preloader.onload = () => {
        if (currentReq === loadRequestId && activeImgUri === imgUri) {
          tooltip.src = imgUri;
          positionTooltip(lastClientX, lastClientY);
          tooltip.style.display = 'block';
        }
      };

      preloader.onerror = () => {
        if (currentReq === loadRequestId) {
          tooltip.style.display = 'none';
        }
      };
    });

    document.addEventListener('mousemove', (e) => {
      lastClientX = e.clientX;
      lastClientY = e.clientY;
      if (tooltip.style.display === 'block') {
        positionTooltip(lastClientX, lastClientY);
      }
    });

    document.addEventListener('mouseout', (e) => {
      const target = e.target.closest('[data-card-image]');
      if (!target) return;

      const related = e.relatedTarget ? e.relatedTarget.closest('[data-card-image]') : null;
      if (!related) {
        activeImgUri = '';
        loadRequestId++;
        tooltip.style.display = 'none';
        tooltip.removeAttribute('src');
      }
    });
  }

  // 3. Mobile Sidebar Toggle
  const mobileToggle = document.getElementById('mobile-menu-button');
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebar-backdrop');

  if (mobileToggle && sidebar) {
    mobileToggle.addEventListener('click', () => {
      const activeSearch = document.getElementById('global-search-container');
      const sBackdrop = document.getElementById('search-backdrop');
      const mOpen = document.getElementById('mobile-search-open');
      if (window.innerWidth < 640 && activeSearch && activeSearch.classList.contains('flex')) {
        activeSearch.classList.add('hidden');
        activeSearch.classList.remove('flex');
        if (sBackdrop) sBackdrop.classList.add('hidden');
        if (mOpen) mOpen.classList.remove('hidden');
      }
      sidebar.classList.toggle('-translate-x-full');
      if (backdrop) backdrop.classList.toggle('hidden');
    });

    if (backdrop) {
      backdrop.addEventListener('click', () => {
        sidebar.classList.add('-translate-x-full');
        backdrop.classList.add('hidden');
      });
    }
  }

  // 4. Dark Mode Theme Toggle
  const themeToggleBtn = document.getElementById('theme-toggle');
  const darkIcon = document.getElementById('theme-toggle-dark-icon');
  const lightIcon = document.getElementById('theme-toggle-light-icon');
  const themeText = document.getElementById('theme-toggle-text');
  const themeTrack = document.getElementById('theme-toggle-track');
  const themeThumb = document.getElementById('theme-toggle-thumb');

  function updateThemeUI(isDark) {
    if (darkIcon && lightIcon) {
      if (isDark) {
        darkIcon.classList.remove('hidden');
        lightIcon.classList.add('hidden');
      } else {
        darkIcon.classList.add('hidden');
        lightIcon.classList.remove('hidden');
      }
    }
    if (themeText) {
      themeText.textContent = isDark ? 'Dark Mode' : 'Light Mode';
    }
    if (themeToggleBtn) {
      themeToggleBtn.setAttribute('aria-checked', isDark ? 'true' : 'false');
      themeToggleBtn.setAttribute('title', isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode');
    }
    if (themeTrack && themeThumb) {
      if (isDark) {
        themeTrack.classList.add('bg-zinc-800');
        themeTrack.classList.remove('bg-zinc-300');
        themeThumb.classList.add('translate-x-4', 'bg-emerald-400');
        themeThumb.classList.remove('translate-x-0', 'bg-amber-500');
      } else {
        themeTrack.classList.remove('bg-zinc-800');
        themeTrack.classList.add('bg-zinc-300');
        themeThumb.classList.remove('translate-x-4', 'bg-emerald-400');
        themeThumb.classList.add('translate-x-0', 'bg-amber-500');
      }
    }
  }

  // Sync UI immediately with current state
  updateThemeUI(document.documentElement.classList.contains('dark'));

  if (themeToggleBtn) {
    themeToggleBtn.addEventListener('click', () => {
      const willBeDark = !document.documentElement.classList.contains('dark');
      if (willBeDark) {
        document.documentElement.classList.add('dark');
        try { localStorage.setItem('modometa-theme', 'dark'); } catch (e) {}
      } else {
        document.documentElement.classList.remove('dark');
        try { localStorage.setItem('modometa-theme', 'light'); } catch (e) {}
      }
      updateThemeUI(willBeDark);
    });
  }

  // 5. Global Search Autocomplete (Archetypes & Players)
  const searchContainer = document.getElementById('global-search-container');
  const searchInput = document.getElementById('global-search-input');
  const searchDropdown = document.getElementById('global-search-dropdown');
  const mobileSearchOpen = document.getElementById('mobile-search-open');
  const mobileSearchClose = document.getElementById('mobile-search-close');
  const searchBackdrop = document.getElementById('search-backdrop');

  if (searchContainer && searchInput && searchDropdown) {
    let searchData = null;
    let isFetching = false;
    let selectedIndex = -1;
    const activeFormat = (searchContainer.getAttribute('data-active-format') || '').toLowerCase();

    function openMobileSearch() {
      if (window.innerWidth < 640) {
        if (mobileSearchOpen) mobileSearchOpen.classList.add('hidden');
        searchContainer.classList.remove('hidden');
        searchContainer.classList.add('flex');
        if (searchBackdrop) searchBackdrop.classList.remove('hidden');
        searchInput.focus();
        setTimeout(() => {
          searchInput.focus();
        }, 30);
        if (searchInput.value.trim()) {
          ensureSearchData(() => {
            renderResults(searchInput.value.trim());
          });
        }
      }
    }

    function closeMobileSearch() {
      if (window.innerWidth < 640) {
        if (mobileSearchOpen) mobileSearchOpen.classList.remove('hidden');
        searchContainer.classList.add('hidden');
        searchContainer.classList.remove('flex');
        if (searchBackdrop) searchBackdrop.classList.add('hidden');
        searchDropdown.classList.add('hidden');
        selectedIndex = -1;
        searchInput.blur();
      }
    }

    if (mobileSearchOpen) {
      mobileSearchOpen.addEventListener('click', () => {
        openMobileSearch();
      });
    }

    if (mobileSearchClose) {
      mobileSearchClose.addEventListener('click', () => {
        closeMobileSearch();
      });
    }

    if (searchBackdrop) {
      searchBackdrop.addEventListener('click', () => {
        closeMobileSearch();
      });
    }

    window.addEventListener('resize', () => {
      if (window.innerWidth >= 640) {
        if (mobileSearchOpen) mobileSearchOpen.classList.remove('hidden');
        searchContainer.classList.add('hidden');
        searchContainer.classList.remove('flex');
        if (searchBackdrop) searchBackdrop.classList.add('hidden');
      }
    });

    function normalizeSearch(str) {
      return (str || '')
        .toLowerCase()
        .normalize('NFKD')
        .replace(/[\u0300-\u036f]/g, '')
        .replace(/['’]/g, '')
        .replace(/[^a-z0-9]/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
    }

    function prepareSearchIndex(data) {
      if (!data || data._prepared) return;
      data._prepared = true;

      for (let i = 0; i < (data.cards || []).length; i++) {
        const item = data.cards[i];
        const c = typeof item === 'string' ? { name: item, slug: '' } : item;
        const cName = c.name || '';
        c.name = cName;
        c.slug = c.slug || '';
        c.norm = normalizeSearch(cName);
        c.lower = cName.toLowerCase();
        c.noSpace = c.norm.replace(/\s+/g, '');
        data.cards[i] = c;
      }

      for (const a of data.archetypes || []) {
        a.norm = normalizeSearch(a.name || '');
        a.lower = (a.name || '').toLowerCase();
        a.fmtLower = (a.format || '').toLowerCase();
        a.noSpace = a.norm.replace(/\s+/g, '');
      }

      for (const p of data.players || []) {
        p.norm = normalizeSearch(p.name || '');
        p.lower = (p.name || '').toLowerCase();
        p.noSpace = p.norm.replace(/\s+/g, '');
      }
    }

    function ensureSearchData(callback) {
      if (searchData) {
        prepareSearchIndex(searchData);
        if (callback) callback(searchData);
        return;
      }
      if (isFetching) return;
      isFetching = true;
      fetch('/api/search-index/')
        .then(res => res.json())
        .then(data => {
          prepareSearchIndex(data);
          searchData = data;
          isFetching = false;
          if (callback) callback(searchData);
        })
        .catch(err => {
          console.error('Failed to load search index:', err);
          isFetching = false;
        });
    }

    // Preload on initial focus or hover
    searchInput.addEventListener('focus', () => {
      ensureSearchData();
    });
    searchInput.addEventListener('mouseenter', () => {
      ensureSearchData();
    });

    // Global keyboard shortcut ('/' or Cmd/Ctrl+K)
    window.addEventListener('keydown', (e) => {
      const activeTag = document.activeElement ? document.activeElement.tagName : '';
      const isInput = activeTag === 'INPUT' || activeTag === 'TEXTAREA' || (document.activeElement && document.activeElement.isContentEditable);
      if (!isInput && e.key === '/') {
        e.preventDefault();
        openMobileSearch();
        searchInput.focus();
        searchInput.select();
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        openMobileSearch();
        searchInput.focus();
        searchInput.select();
      }
    });

    function getResultItems() {
      return searchDropdown.querySelectorAll('[data-search-item]');
    }

    function updateHighlight() {
      const items = getResultItems();
      items.forEach((item, idx) => {
        if (idx === selectedIndex) {
          item.classList.add('bg-zinc-800', 'text-white');
          item.classList.remove('text-zinc-200');
          item.scrollIntoView({ block: 'nearest' });
        } else {
          item.classList.remove('bg-zinc-800', 'text-white');
          item.classList.add('text-zinc-200');
        }
      });
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }

    function renderResults(q) {
      if (!searchData) {
        searchDropdown.innerHTML = '<div class="px-4 py-6 text-center text-xs text-zinc-500">Loading search index...</div>';
        searchDropdown.classList.remove('hidden');
        return;
      }

      prepareSearchIndex(searchData);

      const rawQ = q.toLowerCase();
      const qNorm = normalizeSearch(q);
      const qTokens = qNorm.split(' ').filter(Boolean);
      const qNoSpace = qNorm.replace(/\s+/g, '');

      // Filter & score cards
      const matchedCards = [];
      for (const c of searchData.cards || []) {
        let score = 0;
        if (c.lower === rawQ) {
          score = 500;
        } else if (c.lower.startsWith(rawQ)) {
          score = 350;
        } else if (c.lower.includes(rawQ)) {
          score = 250;
        } else if (c.norm === qNorm) {
          score = 220;
        } else if (c.norm.startsWith(qNorm)) {
          score = 180;
        } else if (c.norm.includes(qNorm)) {
          score = 120;
        } else if (qTokens.length > 1) {
          let inOrder = true;
          let lastIdx = -1;
          for (const tok of qTokens) {
            const idx = c.norm.indexOf(tok, lastIdx + 1);
            if (idx === -1) {
              inOrder = false;
              break;
            }
            lastIdx = idx;
          }
          if (inOrder) {
            score = 80;
          } else if (qTokens.every(tok => c.norm.includes(tok))) {
            score = 60;
          }
        } else if (qNoSpace.length >= 4) {
          if (c.noSpace === qNoSpace) {
            score = 190;
          } else if (c.noSpace.startsWith(qNoSpace)) {
            score = 130;
          } else if (c.noSpace.includes(qNoSpace)) {
            score = 70;
          }
        }

        if (score > 0) {
          matchedCards.push({ ...c, score });
        }
      }
      matchedCards.sort((a, b) => b.score - a.score || a.name.length - b.name.length || a.name.localeCompare(b.name));
      const topCards = matchedCards.slice(0, 6);

      // Filter & score archetypes
      const matchedArchetypes = [];
      for (const a of searchData.archetypes || []) {
        let score = 0;
        if (a.lower === rawQ || a.norm === qNorm) {
          score = 250;
        } else if (a.lower.startsWith(rawQ) || a.norm.startsWith(qNorm)) {
          score = 150;
        } else if (a.lower.includes(rawQ) || a.norm.includes(qNorm)) {
          score = 100;
        } else if (qTokens.length > 1 && qTokens.every(tok => a.norm.includes(tok))) {
          score = 75;
        } else if (qNoSpace.length >= 4 && a.noSpace.includes(qNoSpace)) {
          score = 60;
        } else if (a.fmtLower.includes(rawQ)) {
          score = 20;
        }

        if (score > 0) {
          if (activeFormat && a.fmtLower === activeFormat) score += 50;
          score += Math.min(a.count || 0, 50);
          matchedArchetypes.push({ ...a, score });
        }
      }
      matchedArchetypes.sort((a, b) => b.score - a.score || (b.count || 0) - (a.count || 0));
      const topArchetypes = matchedArchetypes.slice(0, 6);

      // Filter & score players
      const matchedPlayers = [];
      for (const p of searchData.players || []) {
        let score = 0;
        if (p.lower === rawQ || p.norm === qNorm) {
          score = 200;
        } else if (p.lower.startsWith(rawQ) || p.norm.startsWith(qNorm)) {
          score = 120;
        } else if (p.lower.includes(rawQ) || p.norm.includes(qNorm)) {
          score = 80;
        } else if (qTokens.length > 1 && qTokens.every(tok => p.norm.includes(tok))) {
          score = 50;
        } else if (qNoSpace.length >= 4 && p.noSpace.includes(qNoSpace)) {
          score = 40;
        }

        if (score > 0) {
          score += Math.min(p.count || 0, 50);
          matchedPlayers.push({ ...p, score });
        }
      }
      matchedPlayers.sort((a, b) => b.score - a.score || (b.count || 0) - (a.count || 0));
      const topPlayers = matchedPlayers.slice(0, 8);

      if (topCards.length === 0 && topArchetypes.length === 0 && topPlayers.length === 0) {
        searchDropdown.innerHTML = `
          <div class="px-4 py-6 text-center text-xs text-zinc-500">
            No cards, archetypes, or players found matching "<span class="text-zinc-300">${escapeHtml(q)}</span>"
          </div>
        `;
        searchDropdown.classList.remove('hidden');
        selectedIndex = -1;
        return;
      }

      let html = '';

      if (topCards.length > 0) {
        html += `
          <div class="px-3 py-1.5 text-2xs font-bold uppercase tracking-wider text-zinc-500 bg-zinc-950/80 border-b border-zinc-800/60">
            Cards
          </div>
          <div class="divide-y divide-zinc-800/30">
        `;
        for (const c of topCards) {
          const cardSlug = c.slug || c.name.toLowerCase().replace(/[^\w\s-]/g, '').replace(/[\s_-]+/g, '-').replace(/^-+|-+$/g, '') || 'card';
          const url = `/card/${encodeURIComponent(cardSlug)}/`;
          html += `
            <a href="${url}" data-search-item class="flex items-center justify-between px-3.5 py-2 text-xs text-zinc-200 hover:bg-zinc-800/60 hover:text-white transition-colors cursor-pointer" role="option">
              <div class="flex items-center gap-2 truncate min-w-0 pr-2">
                <span class="text-emerald-400 text-xs shrink-0">🃏</span>
                <span class="font-medium truncate text-white">${escapeHtml(c.name)}</span>
              </div>
            </a>
          `;
        }
        html += '</div>';
      }

      if (topArchetypes.length > 0) {
        html += `
          <div class="px-3 py-1.5 text-2xs font-bold uppercase tracking-wider text-zinc-500 bg-zinc-950/80 ${topCards.length > 0 ? 'border-t' : ''} border-b border-zinc-800/60">
            Archetypes
          </div>
          <div class="divide-y divide-zinc-800/30">
        `;
        for (const a of topArchetypes) {
          const url = `/${encodeURIComponent(a.format)}/archetype/${encodeURIComponent(a.slug)}/`;
          const countText = a.count ? `${Number(a.count).toLocaleString()} decks` : '';
          html += `
            <a href="${url}" data-search-item class="flex items-center justify-between px-3.5 py-2 text-xs text-zinc-200 hover:bg-zinc-800/60 hover:text-white transition-colors cursor-pointer" role="option">
              <div class="flex items-center gap-2 truncate min-w-0 pr-2">
                <span class="text-xs shrink-0">🎴</span>
                <span class="font-medium truncate text-white">${escapeHtml(a.name)}</span>
              </div>
              <div class="flex items-center gap-2 shrink-0 text-2xs font-mono text-zinc-500">
                <span class="rounded bg-zinc-800 px-1.5 py-0.5 uppercase tracking-wider text-zinc-300 font-sans">${escapeHtml(a.format)}</span>
                ${countText ? `<span class="hidden sm:inline">${countText}</span>` : ''}
              </div>
            </a>
          `;
        }
        html += '</div>';
      }

      if (topPlayers.length > 0) {
        html += `
          <div class="px-3 py-1.5 text-2xs font-bold uppercase tracking-wider text-zinc-500 bg-zinc-950/80 border-t border-b border-zinc-800/60">
            Players
          </div>
          <div class="divide-y divide-zinc-800/30">
        `;
        for (const p of topPlayers) {
          const url = `/player/${encodeURIComponent(p.name)}/`;
          const countText = p.count ? `${Number(p.count).toLocaleString()} finishes` : '';
          html += `
            <a href="${url}" data-search-item class="flex items-center justify-between px-3.5 py-2 text-xs text-zinc-200 hover:bg-zinc-800/60 hover:text-white transition-colors cursor-pointer" role="option">
              <div class="flex items-center gap-2 truncate min-w-0 pr-2">
                <span class="text-sky-400 text-xs shrink-0">👤</span>
                <span class="font-medium truncate text-white">${escapeHtml(p.name)}</span>
              </div>
              ${countText ? `<span class="text-2xs font-mono text-zinc-500 shrink-0">${countText}</span>` : ''}
            </a>
          `;
        }
        html += '</div>';
      }

      searchDropdown.innerHTML = html;
      searchDropdown.classList.remove('hidden');
      selectedIndex = 0;
      updateHighlight();
    }

    searchInput.addEventListener('input', () => {
      const q = searchInput.value.trim();
      if (!q) {
        searchDropdown.classList.add('hidden');
        searchDropdown.innerHTML = '';
        selectedIndex = -1;
        return;
      }
      ensureSearchData(() => {
        renderResults(q);
      });
    });

    searchInput.addEventListener('keydown', (e) => {
      if (searchDropdown.classList.contains('hidden')) {
        if (e.key === 'ArrowDown') {
          const q = searchInput.value.trim();
          if (q) {
            renderResults(q);
          }
        }
        return;
      }

      const items = getResultItems();
      if (!items.length) return;

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        selectedIndex = (selectedIndex + 1) % items.length;
        updateHighlight();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        selectedIndex = (selectedIndex - 1 + items.length) % items.length;
        updateHighlight();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (selectedIndex >= 0 && items[selectedIndex]) {
          items[selectedIndex].click();
        } else if (items.length > 0) {
          items[0].click();
        }
      } else if (e.key === 'Escape') {
        e.preventDefault();
        searchDropdown.classList.add('hidden');
        selectedIndex = -1;
        searchInput.blur();
        closeMobileSearch();
      }
    });

    // Close dropdown on click outside
    document.addEventListener('click', (e) => {
      const isInsideSearch = searchContainer.contains(e.target);
      const isSearchOpenBtn = mobileSearchOpen && mobileSearchOpen.contains(e.target);
      if (!isInsideSearch && !isSearchOpenBtn) {
        searchDropdown.classList.add('hidden');
        selectedIndex = -1;
        if (window.innerWidth < 640 && searchContainer.classList.contains('flex')) {
          closeMobileSearch();
        }
      }
    });
  }

  // 6. Horizontal Scroll Initial Alignment (Recent Weeks First)
  function initScrollRight() {
    document.querySelectorAll('[data-scroll-right]').forEach(el => {
      el.scrollLeft = el.scrollWidth;
    });
  }
  initScrollRight();
  requestAnimationFrame(initScrollRight);

  // 7. Format Tabs Handler (Card Detail View)
  const formatTabButtons = document.querySelectorAll('[data-format-tab]');
  if (formatTabButtons.length > 0) {
    formatTabButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const targetFormat = btn.getAttribute('data-format-tab');
        formatTabButtons.forEach(b => {
          const isSelected = b === btn;
          b.setAttribute('aria-selected', isSelected ? 'true' : 'false');
          const countBadge = b.querySelector('span:last-child');
          if (isSelected) {
            b.classList.add('border-emerald-400', 'text-white', 'font-semibold');
            b.classList.remove('border-transparent', 'text-zinc-400');
            if (countBadge) {
              countBadge.classList.add('bg-emerald-500/10', 'text-emerald-400', 'border', 'border-emerald-500/20');
              countBadge.classList.remove('bg-zinc-800', 'text-zinc-400');
            }
          } else {
            b.classList.remove('border-emerald-400', 'text-white', 'font-semibold');
            b.classList.add('border-transparent', 'text-zinc-400');
            if (countBadge) {
              countBadge.classList.remove('bg-emerald-500/10', 'text-emerald-400', 'border', 'border-emerald-500/20');
              countBadge.classList.add('bg-zinc-800', 'text-zinc-400');
            }
          }
        });

        document.querySelectorAll('[data-format-panel]').forEach(panel => {
          if (panel.getAttribute('data-format-panel') === targetFormat) {
            panel.classList.remove('hidden');
          } else {
            panel.classList.add('hidden');
          }
        });
      });
    });
  }
});

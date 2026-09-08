/**
 * Modometa client script:
 * - LocalStorage timeframe persistence (30d vs 90d)
 * - Lightweight Scryfall card preview tooltip on hover
 * - Mobile sidebar drawer navigation toggle
 */

document.addEventListener('DOMContentLoaded', () => {
  // 1. Timeframe (30d vs 90d) Persistence (excluded on informational pages like /faq/)
  if (!window.location.pathname.startsWith('/faq')) {
    const savedTimeframe = localStorage.getItem('modometa_timeframe');
    const urlParams = new URLSearchParams(window.location.search);
    const currentDays = urlParams.get('days');

    if (savedTimeframe && !currentDays && (savedTimeframe === '30' || savedTimeframe === '90')) {
      if (savedTimeframe !== '90') {
        urlParams.set('days', savedTimeframe);
        window.location.search = urlParams.toString();
      }
    }
  }

  // Bind timeframe switcher buttons
  document.querySelectorAll('[data-timeframe-toggle]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      const val = btn.getAttribute('data-timeframe-toggle');
      localStorage.setItem('modometa_timeframe', val);
      const params = new URLSearchParams(window.location.search);
      params.set('days', val);
      window.location.search = params.toString();
    });
  });

  // 2. Scryfall Card Hover Tooltip
  const tooltip = document.getElementById('card-hover-tooltip');
  if (tooltip) {
    document.addEventListener('mouseover', (e) => {
      const target = e.target.closest('[data-card-image]');
      if (!target) return;

      const imgUri = target.getAttribute('data-card-image');
      if (!imgUri) return;

      tooltip.src = imgUri;
      tooltip.style.display = 'block';
    });

    document.addEventListener('mousemove', (e) => {
      if (tooltip.style.display !== 'block') return;

      const padding = 15;
      let x = e.clientX + 20;
      let y = e.clientY - 140;

      // Bound checking
      if (x + 270 > window.innerWidth) {
        x = e.clientX - 280;
      }
      if (y + 360 > window.innerHeight) {
        y = window.innerHeight - 370;
      }
      if (y < 10) y = 10;

      tooltip.style.left = `${x}px`;
      tooltip.style.top = `${y}px`;
    });

    document.addEventListener('mouseout', (e) => {
      const target = e.target.closest('[data-card-image]');
      if (target) {
        tooltip.style.display = 'none';
      }
    });
  }

  // 3. Mobile Sidebar Toggle
  const mobileToggle = document.getElementById('mobile-menu-button');
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebar-backdrop');

  if (mobileToggle && sidebar) {
    mobileToggle.addEventListener('click', () => {
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

  if (searchContainer && searchInput && searchDropdown) {
    let searchData = null;
    let isFetching = false;
    let selectedIndex = -1;
    const activeFormat = (searchContainer.getAttribute('data-active-format') || '').toLowerCase();

    function ensureSearchData(callback) {
      if (searchData) {
        if (callback) callback(searchData);
        return;
      }
      if (isFetching) return;
      isFetching = true;
      fetch('/api/search-index/')
        .then(res => res.json())
        .then(data => {
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
        searchInput.focus();
        searchInput.select();
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
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

      const qLower = q.toLowerCase();

      // Filter & score archetypes
      const matchedArchetypes = [];
      for (const a of searchData.archetypes || []) {
        const nameLower = a.name.toLowerCase();
        const fmtLower = (a.format || '').toLowerCase();
        if (nameLower.includes(qLower) || fmtLower.includes(qLower)) {
          let score = 0;
          if (nameLower.startsWith(qLower)) score += 100;
          if (activeFormat && fmtLower === activeFormat) score += 50;
          score += Math.min(a.count || 0, 50);
          matchedArchetypes.push({ ...a, score });
        }
      }
      matchedArchetypes.sort((a, b) => b.score - a.score);
      const topArchetypes = matchedArchetypes.slice(0, 6);

      // Filter & score players
      const matchedPlayers = [];
      for (const p of searchData.players || []) {
        const nameLower = p.name.toLowerCase();
        if (nameLower.includes(qLower)) {
          let score = 0;
          if (nameLower.startsWith(qLower)) score += 100;
          score += Math.min(p.count || 0, 50);
          matchedPlayers.push({ ...p, score });
        }
      }
      matchedPlayers.sort((a, b) => b.score - a.score);
      const topPlayers = matchedPlayers.slice(0, 8);

      if (topArchetypes.length === 0 && topPlayers.length === 0) {
        searchDropdown.innerHTML = `
          <div class="px-4 py-6 text-center text-xs text-zinc-500">
            No archetypes or players found matching "<span class="text-zinc-300">${escapeHtml(q)}</span>"
          </div>
        `;
        searchDropdown.classList.remove('hidden');
        selectedIndex = -1;
        return;
      }

      let html = '';

      if (topArchetypes.length > 0) {
        html += `
          <div class="px-3 py-1.5 text-2xs font-bold uppercase tracking-wider text-zinc-500 bg-zinc-950/80 border-b border-zinc-800/60">
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
      }
    });

    // Close dropdown on click outside
    document.addEventListener('click', (e) => {
      if (!searchContainer.contains(e.target)) {
        searchDropdown.classList.add('hidden');
        selectedIndex = -1;
      }
    });
  }
});

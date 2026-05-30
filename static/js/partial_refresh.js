(function () {
  'use strict';

  function activeInputWithin(container) {
    const active = document.activeElement;

    return Boolean(
      active
      && container.contains(active)
      && active.matches('input, select, textarea')
    );
  }

  function hasOpenActionForm(container) {
    return Boolean(container.querySelector('details.action-details[open]'));
  }

  function hasOpenCollapse(container) {
    return Boolean(container.querySelector('.collapse.show'));
  }

  function hasRefreshPause(container) {
    return Boolean(container.querySelector('[data-refresh-pause][open]'));
  }

  function openDetailsKeys(container) {
    return Array.from(
      container.querySelectorAll('details[open][data-refresh-key]')
    ).map(function (details) {
      return details.dataset.refreshKey;
    });
  }

  function restoreDetails(container, keys) {
    keys.forEach(function (key) {
      const details = container.querySelector(
        `details[data-refresh-key="${CSS.escape(key)}"]`
      );

      if (details) {
        details.open = true;
      }
    });
  }

  function extractRefreshHtml(container, responseText) {
    if (!container.id) {
      return responseText;
    }

    const parsed = new DOMParser().parseFromString(responseText, 'text/html');
    const replacement = parsed.getElementById(container.id);

    return replacement ? replacement.innerHTML : responseText;
  }

  function configureRefresh(container) {
    const url = container.dataset.refreshUrl || container.dataset.partialRefreshUrl;
    const interval = Number(
      container.dataset.refreshMs || container.dataset.partialRefreshMs
    );
    let inProgress = false;

    if (!url || !Number.isFinite(interval) || interval < 1000) {
      return;
    }

    async function refresh() {
      if (
        inProgress
        || document.hidden
        || activeInputWithin(container)
        || hasOpenActionForm(container)
        || hasOpenCollapse(container)
        || hasRefreshPause(container)
      ) {
        return;
      }

      inProgress = true;

      try {
        const response = await fetch(url, {
          cache: 'no-store',
          credentials: 'same-origin',
          headers: {
            'X-Requested-With': 'XMLHttpRequest',
          },
        });

        if (!response.ok) {
          return;
        }

        const openKeys = openDetailsKeys(container);
        const scrollLeft = window.scrollX;
        const scrollTop = window.scrollY;
        container.innerHTML = extractRefreshHtml(
          container,
          await response.text()
        );
        restoreDetails(container, openKeys);
        window.scrollTo(scrollLeft, scrollTop);
      } catch (error) {
        console.debug('No se pudo actualizar el panel parcial.', error);
      } finally {
        inProgress = false;
      }
    }

    window.setInterval(refresh, interval);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) {
        refresh();
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-refresh-url], [data-partial-refresh-url]').forEach(
      configureRefresh
    );
  });
})();

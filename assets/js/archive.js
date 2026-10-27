/* Archivio del diario: filtri per anno, ricerca e caricamento progressivo */
(function () {
  "use strict";

  var grid = document.getElementById("archive-grid");
  if (!grid) return;

  var cards = Array.prototype.slice.call(grid.querySelectorAll(".news-card"));
  if (!cards.length) return;

  var filterButtons = Array.prototype.slice.call(document.querySelectorAll(".filter-btn"));
  var searchInput = document.getElementById("archive-search");
  var countLabel = document.getElementById("archive-count");
  var moreButton = document.getElementById("archive-more");
  var emptyMessage = document.getElementById("archive-empty");

  var pageSize = 12;
  var visibleCount = pageSize;
  var currentYear = "all";
  var currentQuery = "";
  var searchTimer = null;

  var params = new URLSearchParams(window.location.search);
  if (params.get("q")) {
    currentQuery = params.get("q").trim();
    if (searchInput) searchInput.value = currentQuery;
  }
  if (params.get("anno")) {
    currentYear = params.get("anno");
  }

  function matches(card) {
    if (currentYear !== "all" && card.getAttribute("data-year") !== currentYear) {
      return false;
    }
    if (!currentQuery) return true;
    var haystack = (card.getAttribute("data-text") || card.textContent || "").toLowerCase();
    return haystack.indexOf(currentQuery.toLowerCase()) !== -1;
  }

  function syncUrl() {
    if (!window.history || !window.history.replaceState) return;
    var next = new URL(window.location.href);
    if (currentQuery) {
      next.searchParams.set("q", currentQuery);
    } else {
      next.searchParams.delete("q");
    }
    if (currentYear !== "all") {
      next.searchParams.set("anno", currentYear);
    } else {
      next.searchParams.delete("anno");
    }
    window.history.replaceState({}, "", next.pathname + (next.search ? next.search : ""));
  }

  function render() {
    var filtered = cards.filter(matches);
    visibleCount = Math.max(pageSize, Math.min(visibleCount, filtered.length || pageSize));

    cards.forEach(function (card) {
      card.classList.add("is-hidden");
    });
    filtered.slice(0, visibleCount).forEach(function (card) {
      card.classList.remove("is-hidden");
    });

    if (countLabel) {
      countLabel.textContent = filtered.length === 1
        ? "1 storia trovata"
        : filtered.length + " storie trovate";
    }

    if (moreButton) {
      moreButton.parentElement.hidden = visibleCount >= filtered.length;
    }

    if (emptyMessage) {
      emptyMessage.hidden = filtered.length !== 0;
    }

    syncUrl();
  }

  filterButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      filterButtons.forEach(function (item) {
        item.classList.remove("is-active");
      });
      button.classList.add("is-active");
      currentYear = button.getAttribute("data-year") || "all";
      visibleCount = pageSize;
      render();
    });

    if (button.getAttribute("data-year") === currentYear) {
      filterButtons.forEach(function (item) {
        item.classList.remove("is-active");
      });
      button.classList.add("is-active");
    }
  });

  if (searchInput) {
    searchInput.addEventListener("input", function () {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(function () {
        currentQuery = searchInput.value.trim();
        visibleCount = pageSize;
        render();
      }, 140);
    });
  }

  if (moreButton) {
    moreButton.addEventListener("click", function () {
      visibleCount += pageSize;
      render();
    });
  }

  render();
})();

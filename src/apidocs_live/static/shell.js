/* Shell behaviour: tabs, cross-spec search, lazy operation detail, live reload.
   Search runs against /index.json in the browser, so it also works in the
   static build where there is no server to ask. */

(function () {
  var config = window.APIDOCS || {};
  var base = config.base || "";

  function url(path) {
    return base + "/" + String(path).replace(/^\//, "");
  }

  /* ---------- tabs ---------- */

  document.querySelectorAll("[data-tabs]").forEach(function (group) {
    var buttons = group.querySelectorAll("[data-tab]");

    function activate(name, push) {
      buttons.forEach(function (button) {
        var selected = button.dataset.tab === name;
        button.setAttribute("aria-selected", selected ? "true" : "false");
        var panel = document.getElementById("panel-" + button.dataset.tab);
        if (!panel) return;
        panel.hidden = !selected;
        // Only load the renderer iframe once its tab is actually opened.
        var frame = panel.querySelector("iframe[data-src]");
        if (selected && frame && !frame.src) frame.src = frame.dataset.src;
      });
      if (push) history.replaceState(null, "", "#" + name);
    }

    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        activate(button.dataset.tab, true);
      });
    });

    var initial = location.hash.replace("#", "");
    var known = Array.prototype.some.call(buttons, function (b) {
      return b.dataset.tab === initial;
    });
    activate(known ? initial : buttons[0].dataset.tab, false);
  });

  /* ---------- operation detail, fetched on first expand ---------- */

  document.querySelectorAll("details.op").forEach(function (item) {
    item.addEventListener("toggle", function () {
      if (!item.open || item.dataset.loaded) return;
      item.dataset.loaded = "1";
      var target = item.querySelector("[data-detail]");
      if (!target) return;

      fetch(url("operation/" + encodeURIComponent(item.dataset.op) + ".json"))
        .then(function (response) {
          if (!response.ok) throw new Error(response.status);
          return response.json();
        })
        .then(function (detail) {
          target.textContent = JSON.stringify(detail, null, 2);
        })
        .catch(function () {
          target.textContent = "Could not load this operation.";
        });
    });
  });

  /* ---------- cross-spec search ---------- */

  var input = document.getElementById("search-input");
  var results = document.getElementById("search-results");

  if (input && results) {
    var operations = null;

    function load() {
      if (operations) return Promise.resolve(operations);
      return fetch(url("index.json"))
        .then(function (response) {
          return response.json();
        })
        .then(function (payload) {
          operations = payload.operations || [];
          return operations;
        })
        .catch(function () {
          operations = [];
          return operations;
        });
    }

    function score(operation, terms) {
      var fields = [
        [operation.id.toLowerCase(), 6],
        [operation.path.toLowerCase(), 5],
        [(operation.summary || "").toLowerCase(), 4],
        [(operation.tags || []).join(" ").toLowerCase(), 3],
        [operation.app.toLowerCase(), 2],
      ];
      var total = 0;
      fields.forEach(function (field) {
        terms.forEach(function (term) {
          if (field[0].indexOf(term) !== -1) total += field[1];
        });
      });
      return total;
    }

    function render(matches) {
      results.innerHTML = "";
      if (!matches.length) {
        results.innerHTML = '<li class="search-empty">No matching operation.</li>';
        return;
      }
      matches.forEach(function (operation) {
        var li = document.createElement("li");
        var link = document.createElement("a");
        link.href =
          url(encodeURIComponent(operation.app) + "/?op=" + encodeURIComponent(operation.id)) +
          "#operations";
        link.innerHTML =
          '<strong>' + operation.method + "</strong> " + escapeHtml(operation.summary || operation.id) +
          '<span class="sr-path">' + escapeHtml(operation.path) + " · " + operation.app + "</span>";
        li.appendChild(link);
        results.appendChild(li);
      });
    }

    function escapeHtml(value) {
      var div = document.createElement("div");
      div.textContent = value == null ? "" : String(value);
      return div.innerHTML;
    }

    var timer = null;
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var query = input.value.trim().toLowerCase();
        if (!query) {
          results.innerHTML = "";
          return;
        }
        var terms = query.split(/\s+/);
        load().then(function (all) {
          var matches = all
            .map(function (operation) {
              return { operation: operation, score: score(operation, terms) };
            })
            .filter(function (row) {
              return row.score > 0;
            })
            .sort(function (a, b) {
              return b.score - a.score;
            })
            .slice(0, 12)
            .map(function (row) {
              return row.operation;
            });
          render(matches);
        });
      }, 90);
    });
  }

  /* ---------- deep link from search: open and scroll to an operation ---------- */

  var wanted = new URLSearchParams(location.search).get("op");
  if (wanted) {
    var target = document.querySelector('details.op[data-op="' + CSS.escape(wanted) + '"]');
    if (target) {
      target.open = true;
      target.scrollIntoView({ block: "center" });
    }
  }

  /* ---------- live reload ---------- */

  if (config.watch && typeof EventSource !== "undefined") {
    var revision = config.revision;
    var events = new EventSource(url("events"));
    events.addEventListener("revision", function (event) {
      if (revision && event.data && event.data !== revision) location.reload();
      revision = event.data;
    });
  }
})();

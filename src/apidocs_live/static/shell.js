/* Shell behaviour, shared by the RapiDoc pages and the plain ones.

   Three jobs: move between APIs, search across all of them, and apply live
   updates. On a RapiDoc page an update is pushed straight into the element
   with loadSpec(), so the reader keeps their scroll position instead of
   watching the page reload underneath them. */

(function () {
  var config = window.APIDOCS || {};
  var base = config.base || "";

  function url(path) {
    return base + "/" + String(path).replace(/^\//, "");
  }

  function escapeHtml(value) {
    var div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  /* ---------- switch between APIs ---------- */

  var switcher = document.getElementById("api-switch");
  if (switcher) {
    switcher.addEventListener("change", function () {
      if (switcher.value) location.href = switcher.value;
    });
  }

  /* ---------- search across every spec ---------- */

  var input = document.getElementById("search-input");
  var results = document.getElementById("search-results");

  if (input && results) {
    var operations = null;

    function loadIndex() {
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
        // Same API: jump within the rendered page. Different API: navigate.
        link.href =
          url(encodeURIComponent(operation.app) + "/") +
          "?op=" +
          encodeURIComponent(operation.method + " " + operation.path);
        link.innerHTML =
          "<strong>" + escapeHtml(operation.method) + "</strong> " +
          escapeHtml(operation.summary || operation.id) +
          '<span class="sr-path">' + escapeHtml(operation.path) + " · " + escapeHtml(operation.app) + "</span>";

        if (operation.app === config.app) {
          link.addEventListener("click", function (event) {
            event.preventDefault();
            goToOperation(operation.method, operation.path);
            results.innerHTML = "";
            input.value = "";
          });
        }
        li.appendChild(link);
        results.appendChild(li);
      });
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
        loadIndex().then(function (all) {
          render(
            all
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
              })
          );
        });
      }, 90);
    });

    document.addEventListener("click", function (event) {
      if (!results.contains(event.target) && event.target !== input) results.innerHTML = "";
    });
  }

  /* ---------- the RapiDoc element ---------- */

  var docs = document.getElementById("docs");
  if (!docs) return;

  function applyTheme() {
    if (config.theme !== "auto") return;
    var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    var palette = dark
      ? { theme: "dark", bg: "#1e1e1e", text: "#d4d4d4", nav: "#252526", navText: "#bbbbbb", hover: "#37373d", accent: "#4daafc" }
      : { theme: "light", bg: "#ffffff", text: "#1a1d21", nav: "#f3f3f3", navText: "#3b4048", hover: "#e4e6e9", accent: "#0066b8" };

    docs.setAttribute("theme", palette.theme);
    docs.setAttribute("bg-color", palette.bg);
    docs.setAttribute("text-color", palette.text);
    docs.setAttribute("nav-bg-color", palette.nav);
    docs.setAttribute("nav-text-color", palette.navText);
    docs.setAttribute("nav-hover-bg-color", palette.hover);
    docs.setAttribute("nav-accent-color", palette.accent);
    docs.setAttribute("primary-color", palette.accent);
    document.body.style.background = palette.bg;
  }

  applyTheme();
  if (config.theme === "auto" && window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
  }

  function goToOperation(method, path) {
    // RapiDoc builds an operation's element id as `<method>-<path>`, replacing
    // only [\s#:?&={}] with hyphens — slashes survive. See spec-parser.js.
    var slug = path.replace(/[\s#:?&={}]/g, "-");
    if (typeof docs.scrollToPath === "function") {
      docs.scrollToPath(method.toLowerCase() + "-" + slug);
    }
  }

  function pendingOperation() {
    var wanted = new URLSearchParams(location.search).get("op");
    if (!wanted) return;
    var parts = wanted.split(" ");
    if (parts.length === 2) goToOperation(parts[0], parts[1]);
  }

  docs.addEventListener("spec-loaded", function () {
    // The nav is only built once the spec is in, so deep links wait for it.
    setTimeout(pendingOperation, 60);
  });

  // The element has to be upgraded before it has loadSpec on it.
  function loadIntoRenderer(source) {
    if (typeof docs.loadSpec === "function") {
      docs.loadSpec(source);
    } else if (window.customElements) {
      customElements.whenDefined("rapi-doc").then(function () {
        docs.loadSpec(source);
      });
    }
  }

  loadIntoRenderer(config.spec);

  /* ---------- live updates, without losing the reader's place ---------- */

  if (config.watch && typeof EventSource !== "undefined") {
    var revision = config.revision;
    var events = new EventSource(url("events"));

    events.addEventListener("revision", function (event) {
      if (!revision || !event.data || event.data === revision) {
        revision = event.data;
        return;
      }
      revision = event.data;

      // Re-fetch and swap the spec in place. Bust the cache so a changed file
      // is never served from memory.
      loadIntoRenderer(config.spec + "?rev=" + encodeURIComponent(revision));
      flash("Documentation updated");
    });
  }

  function flash(message) {
    var note = document.createElement("div");
    note.textContent = message;
    note.style.cssText =
      "position:fixed;inset-block-end:18px;inset-inline-end:18px;z-index:999;" +
      "padding:8px 14px;border-radius:6px;font:13px " +
      "-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;" +
      "background:#0066b8;color:#fff;box-shadow:0 4px 14px rgba(0,0,0,.25);" +
      "opacity:0;transition:opacity .2s";
    document.body.appendChild(note);
    requestAnimationFrame(function () {
      note.style.opacity = "1";
    });
    setTimeout(function () {
      note.style.opacity = "0";
      setTimeout(function () {
        note.remove();
      }, 300);
    }, 2200);
  }
})();

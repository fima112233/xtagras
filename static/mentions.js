(function () {
  "use strict";

  var ACTIVE_CLASS = "mentionable";
  var dropdown = null;
  var active = null;
  var selected = -1;
  var currentItems = [];

  function getCaretWord(textarea) {
    var value = textarea.value;
    var pos = textarea.selectionStart;
    var before = value.slice(0, pos);
    var match = before.match(/@([\w]*)$/);
    if (!match) return null;
    return { start: pos - match[0].length, query: match[1] };
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function ensureDropdown() {
    if (dropdown) return dropdown;
    dropdown = document.createElement("div");
    dropdown.id = "mention-dropdown";
    dropdown.className = "mention-dropdown";
    dropdown.style.display = "none";
    document.body.appendChild(dropdown);
    return dropdown;
  }

  function hideDropdown() {
    if (dropdown) dropdown.style.display = "none";
    active = null;
    selected = -1;
    currentItems = [];
  }

  function highlightItem() {
    if (!dropdown) return;
    var items = dropdown.querySelectorAll(".mention-item");
    items.forEach(function (el, i) {
      if (i === selected) el.classList.add("active");
      else el.classList.remove("active");
    });
    if (items[selected]) items[selected].scrollIntoView({ block: "nearest" });
  }

  function showDropdown(textarea, query) {
    fetch("/api/users?q=" + encodeURIComponent(query))
      .then(function (r) { return r.json(); })
      .then(function (users) {
        if (active !== textarea) return;
        if (!users.length) { hideDropdown(); return; }
        var dd = ensureDropdown();
        currentItems = users;
        selected = 0;
        dd.innerHTML = "";
        users.forEach(function (u) {
          var item = document.createElement("button");
          item.type = "button";
          item.className = "mention-item";
          var dn = u.display_name || u.username;
          var badge = u.is_verified ? ' <span class="badge">✓</span>' : "";
          item.innerHTML =
            '<span class="mi-avatar" style="background:' + escapeHtml(u.avatar_color || "#6c5ce7") + '">' +
            escapeHtml((dn[0] || "").toUpperCase()) + "</span>" +
            '<span class="mi-name">' + escapeHtml(dn) + badge + "</span>" +
            '<span class="mi-handle">@' + escapeHtml(u.username) + "</span>";
          item.addEventListener("mousedown", function (e) {
            e.preventDefault();
            insertMention(textarea, u.username);
          });
          dd.appendChild(item);
        });
        var rect = textarea.getBoundingClientRect();
        dd.style.display = "block";
        dd.style.top = (rect.bottom + 4) + "px";
        dd.style.left = rect.left + "px";
        dd.style.width = Math.max(rect.width, 260) + "px";
        active = textarea;
        highlightItem();
      });
  }

  function insertMention(textarea, username) {
    var info = getCaretWord(textarea);
    if (!info) return;
    var value = textarea.value;
    textarea.value =
      value.slice(0, info.start) + "@" + username + " " + value.slice(textarea.selectionStart);
    var pos = info.start + username.length + 2;
    textarea.focus();
    textarea.setSelectionRange(pos, pos);
    hideDropdown();
  }

  document.addEventListener("input", function (e) {
    var ta = e.target;
    if (!(ta instanceof HTMLTextAreaElement)) return;
    if (!ta.classList.contains(ACTIVE_CLASS)) return;
    var info = getCaretWord(ta);
    if (info) {
      active = ta;
      showDropdown(ta, info.query);
    } else {
      hideDropdown();
    }
  });

  document.addEventListener("click", function (e) {
    if (!e.target.closest(".mention-dropdown")) hideDropdown();
  });

  document.addEventListener("keydown", function (e) {
    if (!dropdown || dropdown.style.display === "none" || !active) return;
    var items = dropdown.querySelectorAll(".mention-item");
    if (!items.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      selected = (selected + 1) % items.length;
      highlightItem();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      selected = (selected - 1 + items.length) % items.length;
      highlightItem();
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      var u = currentItems[selected];
      if (u) insertMention(active, u.username);
    } else if (e.key === "Escape") {
      hideDropdown();
    }
  });

  window.addEventListener("scroll", hideDropdown, true);
  window.addEventListener("resize", hideDropdown);
})();

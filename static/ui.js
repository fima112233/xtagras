(function () {
  "use strict";

  var EMOJIS = ["😀","😂","😍","😎","😢","😡","👍","👎","🔥","❤️","🎉","💯","🤔","🙏","👀","✨"];

  function buildPicker() {
    var picker = document.createElement("div");
    picker.className = "emoji-popover";
    EMOJIS.forEach(function (e) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = e;
      picker.appendChild(b);
    });
    document.body.appendChild(picker);
    return picker;
  }

  var picker = buildPicker();
  var activeTextarea = null;

  function placePicker(btn) {
    var rect = btn.getBoundingClientRect();
    picker.style.left = Math.max(8, rect.left - 80) + "px";
    picker.style.top = (rect.bottom + 6) + "px";
  }

  function insertEmoji(textarea, emoji) {
    var pos = textarea.selectionStart;
    var value = textarea.value;
    textarea.value = value.slice(0, pos) + emoji + value.slice(textarea.selectionEnd);
    var newPos = pos + emoji.length;
    textarea.focus();
    textarea.setSelectionRange(newPos, newPos);
  }

  document.addEventListener("click", function (e) {
    var toggle = e.target.closest(".emoji-toggle");
    if (toggle) {
      e.preventDefault();
      activeTextarea = toggle.closest("form").querySelector("textarea");
      if (picker.classList.contains("open")) {
        picker.classList.remove("open");
      } else {
        placePicker(toggle);
        picker.classList.add("open");
      }
      return;
    }
    if (e.target.closest(".emoji-popover")) {
      if (e.target.tagName === "BUTTON" && activeTextarea) {
        e.preventDefault();
        insertEmoji(activeTextarea, e.target.textContent);
        activeTextarea.focus();
      }
      return;
    }
    picker.classList.remove("open");
  });

  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".copy-link");
    if (!btn) return;
    var url = new URL(btn.dataset.url, location.origin).href;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(function () {
        var old = btn.textContent;
        btn.textContent = "✓ Скопировано";
        setTimeout(function () { btn.textContent = old; }, 1500);
      });
    }
  });
})();

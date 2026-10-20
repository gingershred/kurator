// kurator – the little JavaScript there is: embedded player bridge and keyboard shortcuts.
(function () {
  "use strict";

  function csrfToken() {
    try {
      return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRF-Token"] || "";
    } catch (e) {
      return "";
    }
  }

  // --- Embedded player (youtube-nocookie.com via the IFrame API) ---------------------------
  var el = document.getElementById("player");
  if (el) {
    var videoId = el.dataset.video;
    var start = parseInt(el.dataset.start || "0", 10);
    var player = null;
    var timer = null;
    var PROGRESS_MS = 15000;

    var send = function (event) {
      if (!player || typeof player.getCurrentTime !== "function") return;
      var body = JSON.stringify({ event: event, position: Math.floor(player.getCurrentTime() || 0) });
      fetch("/videos/" + encodeURIComponent(videoId) + "/events", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
        body: body,
        keepalive: true,
        credentials: "same-origin",
      }).catch(function () {});
    };

    var stopTimer = function () {
      if (timer) { clearInterval(timer); timer = null; }
    };

    window.onYouTubeIframeAPIReady = function () {
      player = new window.YT.Player("player", {
        host: el.dataset.host,
        videoId: videoId,
        playerVars: { start: start, rel: 0, playsinline: 1, autoplay: 1 },
        events: {
          onStateChange: function (e) {
            var S = window.YT.PlayerState;
            if (e.data === S.PLAYING) {
              send("play");
              stopTimer();
              timer = setInterval(function () { send("progress"); }, PROGRESS_MS);
            } else if (e.data === S.PAUSED) {
              stopTimer();
              send("pause");
            } else if (e.data === S.ENDED) {
              stopTimer();
              send("ended");
            }
          },
        },
      });
    };
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden" && timer) send("progress");
    });

    var tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(tag);
  }

  // --- Keyboard shortcuts -------------------------------------------------------------------
  // j/k: next/previous video, Enter/o: open, w: watched, h: hide, l: watch later,
  // +/-: thumbs up/down, ?: help. The action keys click the button with a matching data-key.
  // The selection is kept by id, because HTMX replaces a card after each action.
  var currentId = null;

  var cards = function () {
    return Array.prototype.slice.call(document.querySelectorAll("article.video"));
  };

  var currentCard = function () {
    return currentId ? document.getElementById(currentId) : null;
  };

  var highlight = function (card, scroll) {
    var old = document.querySelector("article.is-current");
    if (old && old !== card) old.classList.remove("is-current");
    if (!card) return;
    card.classList.add("is-current");
    card.focus({ preventScroll: true });
    if (scroll) card.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };

  var select = function (card) {
    currentId = card ? card.id : null;
    highlight(card, true);
  };

  var target = function () {
    return currentCard() || document.querySelector("article.detail-actions");
  };

  var typing = function (e) {
    var t = e.target;
    return t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
  };

  document.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey || typing(e)) return;
    var key = e.key;
    var help = document.getElementById("shortcuts");
    if (key === "?") {
      if (help) { help.open ? help.close() : help.showModal(); }
      e.preventDefault();
      return;
    }
    if (key === "j" || key === "k") {
      var list = cards();
      if (!list.length) return;
      var index = list.indexOf(currentCard());
      index = key === "j" ? Math.min(index + 1, list.length - 1) : Math.max(index - 1, 0);
      select(list[index]);
      e.preventDefault();
      return;
    }
    var card = target();
    if (!card) return;
    if (key === "Enter" || key === "o") {
      var link = card.querySelector("a.title, a.thumb");
      if (link && currentCard()) { window.location.href = link.href; e.preventDefault(); }
      return;
    }
    var button = card.querySelector('[data-key="' + key + '"]');
    if (button) { button.click(); e.preventDefault(); }
  });

  // HTMX replaces a card after an action: restore the highlight on the new element.
  document.addEventListener("htmx:load", function (e) {
    if (currentId && e.target && e.target.id === currentId) highlight(e.target, false);
  });
})();

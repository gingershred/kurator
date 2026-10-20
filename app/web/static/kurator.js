// kurator – the little JavaScript there is: player bridges and keyboard shortcuts.
(function () {
  "use strict";

  function csrfToken() {
    try {
      return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRF-Token"] || "";
    } catch (e) {
      return "";
    }
  }

  // --- Progress reporting (shared by the embedded and the local player) --------------------
  var PROGRESS_MS = 15000;

  var reporter = function (videoId, position) {
    var timer = null;
    var send = function (event) {
      var pos = position();
      if (pos === null) return;
      fetch("/videos/" + encodeURIComponent(videoId) + "/events", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
        body: JSON.stringify({ event: event, position: Math.floor(pos) }),
        keepalive: true,
        credentials: "same-origin",
      }).catch(function () {});
    };
    var stop = function () {
      if (timer) { clearInterval(timer); timer = null; }
    };
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden" && timer) send("progress");
    });
    return {
      play: function () {
        send("play");
        stop();
        timer = setInterval(function () { send("progress"); }, PROGRESS_MS);
      },
      pause: function () { stop(); send("pause"); },
      ended: function () { stop(); send("ended"); },
    };
  };

  // --- Embedded player (youtube-nocookie.com via the IFrame API) ---------------------------
  var el = document.getElementById("player");
  if (el) {
    var player = null;
    var embedded = reporter(el.dataset.video, function () {
      return player && typeof player.getCurrentTime === "function" ? player.getCurrentTime() || 0 : null;
    });
    window.onYouTubeIframeAPIReady = function () {
      player = new window.YT.Player("player", {
        host: el.dataset.host,
        videoId: el.dataset.video,
        playerVars: { start: parseInt(el.dataset.start || "0", 10), rel: 0, playsinline: 1, autoplay: 1 },
        events: {
          onStateChange: function (e) {
            var S = window.YT.PlayerState;
            if (e.data === S.PLAYING) embedded.play();
            else if (e.data === S.PAUSED) embedded.pause();
            else if (e.data === S.ENDED) embedded.ended();
          },
        },
      });
    };
    var tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(tag);
  }

  // --- Local player (downloaded file in <video>/<audio>) -----------------------------------
  var media = document.getElementById("local-player");
  if (media) {
    var start = parseInt(media.dataset.start || "0", 10);
    var local = reporter(media.dataset.video, function () { return media.currentTime || 0; });
    media.addEventListener("loadedmetadata", function () {
      if (start > 0 && start < media.duration) media.currentTime = start;
    }, { once: true });
    media.addEventListener("play", local.play);
    media.addEventListener("pause", function () { if (!media.ended) local.pause(); });
    media.addEventListener("ended", local.ended);
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

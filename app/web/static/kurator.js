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
})();

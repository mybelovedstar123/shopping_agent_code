// Keeps a run's page up to date. The page works without this, by reloading.
(function () {
  "use strict";
  var region = document.getElementById("live-run");
  if (!region || region.dataset.finished === "true") {
    return;
  }

  // Never replace a form the shopper has started to fill in.
  function shopperIsTyping() {
    if (region.contains(document.activeElement) && document.activeElement.matches("input, textarea")) {
      return true;
    }
    return Array.prototype.some.call(region.querySelectorAll("input:not([type=hidden]), textarea"), function (field) {
      return field.value !== "";
    });
  }

  function refresh() {
    fetch(region.dataset.liveUrl, { credentials: "same-origin" })
      .then(function (reply) {
        if (!reply.ok) {
          throw new Error("HTTP " + reply.status);
        }
        var finished = reply.headers.get("X-Run-Finished") === "true";
        return reply.text().then(function (html) {
          if (!shopperIsTyping()) {
            region.innerHTML = html;
          }
          if (!finished || shopperIsTyping()) {
            window.setTimeout(refresh, 1500);
          }
        });
      })
      .catch(function () {
        window.setTimeout(refresh, 5000);
      });
  }

  window.setTimeout(refresh, 1000);
})();

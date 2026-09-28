/* Status page of the Kanban app (PRD-07): #stopped or #error=<message>; text only, never parsed as HTML. */
(function () {
  "use strict";
  const hash = new URLSearchParams(location.hash.slice(1));
  const title = document.getElementById("title");
  const message = document.getElementById("message");
  if (hash.has("error")) {
    title.textContent = "Kanban could not open the board";
    message.textContent = hash.get("error");
  } else if (hash.has("stopped")) {
    title.textContent = "The board service is stopped";
    message.textContent = "Choose Reload to start it again.";
  }
})();

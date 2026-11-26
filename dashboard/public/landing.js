// Entrance-animation fallback and the phone menu (shared by the landing page and the app).
(function () {
  "use strict";
  var body = document.body;
  var burger = document.querySelector(".burger");
  var backdrop = document.querySelector(".menu-backdrop");
  var nav = document.getElementById("site-nav");
  var all = function () { return document.querySelectorAll(".appear, .hero-photo"); };

  // 1. Each element settles into its final state once its own animation ends.
  Array.prototype.forEach.call(all(), function (el) {
    el.addEventListener("animationend", function (e) {
      if (e.target === el) el.classList.add("is-in");
    }, { once: true });
  });

  // 2. If animations are not running at all (old browser, blocked, print), show everything.
  requestAnimationFrame(function () {
    requestAnimationFrame(function () {
      var probe = document.querySelector(".appear");
      var running = probe && probe.getAnimations && probe.getAnimations().some(function (a) {
        return a.playState === "running" || a.playState === "finished";
      });
      if (!running) Array.prototype.forEach.call(all(), function (el) { el.classList.add("is-in"); });
    });
  });

  // 3-5. Phone menu.
  function setMenu(open) {
    body.classList.toggle("menu-open", open);
    if (burger) {
      burger.setAttribute("aria-expanded", open ? "true" : "false");
      burger.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    }
  }
  if (burger) burger.addEventListener("click", function () { setMenu(!body.classList.contains("menu-open")); });
  if (backdrop) backdrop.addEventListener("click", function () { setMenu(false); });
  if (nav) nav.addEventListener("click", function (e) { if (e.target.closest("a")) setMenu(false); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") setMenu(false); });
  var wide = window.matchMedia("(min-width: 901px)");
  var onWide = function () { if (wide.matches) setMenu(false); };
  if (wide.addEventListener) wide.addEventListener("change", onWide); else wide.addListener(onWide);

  // The hero video is decoration: if it cannot play, the page stays plain black.
  var v = document.getElementById("hero-video");
  if (v) v.addEventListener("error", function () { v.remove(); });
})();

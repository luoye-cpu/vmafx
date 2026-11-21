/* Copyright 2026 Lusoris; SPDX-License-Identifier: EUPL-1.2 */
/* Material owns code-block focus. Only overflowing table wrappers need ours. */
(function () {
  var observer;

  function markScrollable() {
    var regions = document.querySelectorAll(".md-typeset__scrollwrap");
    for (var i = 0; i < regions.length; i += 1) {
      var region = regions[i];
      var managed = region.hasAttribute("data-vx-scroll-focus");
      if (region.scrollWidth > region.clientWidth) {
        if (!region.hasAttribute("tabindex")) {
          region.setAttribute("tabindex", "0");
          region.setAttribute("data-vx-scroll-focus", "");
        }
      } else if (managed) {
        region.removeAttribute("tabindex");
        region.removeAttribute("data-vx-scroll-focus");
      }
      if (observer) {
        observer.observe(region);
      }
    }
  }

  if (typeof ResizeObserver !== "undefined") {
    observer = new ResizeObserver(markScrollable);
  }
  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(function () {
      if (observer) {
        observer.disconnect();
      }
      markScrollable();
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", markScrollable);
  } else {
    markScrollable();
  }
  window.addEventListener("load", markScrollable);
  window.addEventListener("resize", markScrollable);
})();

/* Progetto Rebecca — interazioni del sito */
(function () {
  "use strict";

  var doc = document;
  var body = doc.body;

  /* Header sticky ------------------------------------------------------- */
  var header = doc.getElementById("site-header");
  if (header) {
    var onScroll = function () {
      header.classList.toggle("is-scrolled", window.scrollY > 12);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* Menu mobile --------------------------------------------------------- */
  var navToggle = doc.querySelector(".nav-toggle");
  var nav = doc.getElementById("main-nav");
  if (navToggle && nav) {
    var closeNav = function () {
      nav.classList.remove("is-open");
      navToggle.setAttribute("aria-expanded", "false");
      navToggle.setAttribute("aria-label", "Apri il menu");
    };

    navToggle.addEventListener("click", function () {
      var open = nav.classList.toggle("is-open");
      navToggle.setAttribute("aria-expanded", String(open));
      navToggle.setAttribute("aria-label", open ? "Chiudi il menu" : "Apri il menu");
    });

    nav.addEventListener("click", function (event) {
      if (event.target.closest("a")) {
        closeNav();
      }
    });

    doc.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closeNav();
      }
    });

    doc.addEventListener("click", function (event) {
      if (!nav.contains(event.target) && !navToggle.contains(event.target)) {
        closeNav();
      }
    });
  }

  /* Torna su ------------------------------------------------------------ */
  var backToTop = doc.querySelector(".back-to-top");
  if (backToTop) {
    var toggleBackToTop = function () {
      backToTop.classList.toggle("is-visible", window.scrollY > 600);
    };
    toggleBackToTop();
    window.addEventListener("scroll", toggleBackToTop, { passive: true });
    backToTop.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  /* Reveal on scroll ---------------------------------------------------- */
  var revealItems = doc.querySelectorAll(
    ".section .project-card, .section .value-card, " +
    ".section .contact-card, .section .donate-card, .timeline-item, .project-feature"
  );
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if ("IntersectionObserver" in window && !reduceMotion) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.05 });

    revealItems.forEach(function (item) {
      item.classList.add("reveal");
      observer.observe(item);
    });
  }

  /* Lightbox ------------------------------------------------------------ */
  var lightbox = doc.getElementById("lightbox");
  var lightboxImg = lightbox ? lightbox.querySelector("img") : null;
  var lightboxCaption = lightbox ? lightbox.querySelector("figcaption") : null;
  var lastFocused = null;

  function openLightbox(href, caption) {
    if (!lightbox || !lightboxImg) return;
    lastFocused = doc.activeElement;
    lightboxImg.src = href;
    lightboxImg.alt = caption || "";
    if (lightboxCaption) lightboxCaption.textContent = caption || "";
    lightbox.hidden = false;
    body.classList.add("no-scroll");
    var close = lightbox.querySelector(".lightbox-close");
    if (close) close.focus();
  }

  function closeLightbox() {
    if (!lightbox) return;
    lightbox.hidden = true;
    body.classList.remove("no-scroll");
    if (lightboxImg) {
      lightboxImg.src = "";
      lightboxImg.alt = "";
    }
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  doc.addEventListener("click", function (event) {
    var link = event.target.closest("a.lightbox");
    if (link) {
      event.preventDefault();
      openLightbox(link.getAttribute("href"), link.getAttribute("data-caption") || "");
      return;
    }
    if (event.target.closest(".lightbox-close")) {
      closeLightbox();
      return;
    }
    if (lightbox && !lightbox.hidden && event.target === lightbox) {
      closeLightbox();
    }
  });

  /* Video modal --------------------------------------------------------- */
  var videoModal = doc.getElementById("video-modal");
  var videoInner = videoModal ? videoModal.querySelector(".video-modal-inner") : null;

  function openVideo(id, title) {
    if (!videoModal || !videoInner || !id) return;
    lastFocused = doc.activeElement;
    var iframe = doc.createElement("iframe");
    iframe.src = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(id) +
      "?autoplay=1&rel=0&modestbranding=1";
    iframe.title = title || "Video del Progetto Rebecca";
    iframe.allow = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture";
    iframe.allowFullscreen = true;
    videoInner.innerHTML = "";
    videoInner.appendChild(iframe);
    videoModal.hidden = false;
    body.classList.add("no-scroll");
    var close = videoModal.querySelector(".video-modal-close");
    if (close) close.focus();
  }

  function closeVideo() {
    if (!videoModal || !videoInner) return;
    videoModal.hidden = true;
    videoInner.innerHTML = "";
    body.classList.remove("no-scroll");
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  doc.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-video-id]");
    if (trigger && !trigger.closest(".video-modal")) {
      event.preventDefault();
      openVideo(trigger.getAttribute("data-video-id"), trigger.getAttribute("data-video-title") || "");
      return;
    }
    if (event.target.closest(".video-modal-close")) {
      closeVideo();
      return;
    }
    if (videoModal && !videoModal.hidden && event.target === videoModal) {
      closeVideo();
    }
  });

  doc.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    if (lightbox && !lightbox.hidden) closeLightbox();
    if (videoModal && !videoModal.hidden) closeVideo();
  });

  /* Copia IBAN ---------------------------------------------------------- */
  doc.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy]");
    if (!button) return;
    var value = button.getAttribute("data-copy");
    if (!value) return;
    var done = function () {
      var old = button.textContent;
      button.textContent = "Copiato";
      window.setTimeout(function () { button.textContent = old; }, 1800);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(done).catch(function () {
        window.prompt("Copia l'IBAN:", value);
      });
    } else {
      window.prompt("Copia l'IBAN:", value);
    }
  });
})();

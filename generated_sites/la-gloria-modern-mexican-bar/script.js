(function () {
  "use strict";

  var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var header = document.getElementById("site-header");
  var navToggle = document.getElementById("nav-toggle");
  var navLinks = document.getElementById("nav-links");

  /* Sticky header shadow on scroll */
  function onScroll() {
    if (!header) return;
    if (window.scrollY > 10) {
      header.classList.add("scrolled");
    } else {
      header.classList.remove("scrolled");
    }
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  /* Mobile nav toggle */
  if (navToggle && navLinks) {
    navToggle.addEventListener("click", function () {
      var open = navLinks.classList.toggle("open");
      navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    navLinks.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        navLinks.classList.remove("open");
        navToggle.setAttribute("aria-expanded", "false");
      });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && navLinks.classList.contains("open")) {
        navLinks.classList.remove("open");
        navToggle.setAttribute("aria-expanded", "false");
        navToggle.focus();
      }
    });
  }

  /* Smooth anchor scrolling */
  document.querySelectorAll('a[href^="#"]').forEach(function (anchor) {
    anchor.addEventListener("click", function (e) {
      var id = anchor.getAttribute("href");
      if (id.length < 2) return;
      var target = document.querySelector(id);
      if (!target) return;
      e.preventDefault();
      target.scrollIntoView({
        behavior: reducedMotion ? "auto" : "smooth",
        block: "start"
      });
    });
  });

  /* Scroll reveal */
  var reveals = document.querySelectorAll(".reveal");
  var revealObserver = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("in-view");
          revealObserver.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
  );
  reveals.forEach(function (el) {
    revealObserver.observe(el);
  });

  /* Review slider */
  var track = document.getElementById("review-track");
  var dotsWrap = document.getElementById("review-dots");
  if (track) {
    var slides = track.querySelectorAll(".review");
    var current = 0;
    var timer = null;

    function goTo(index) {
      current = (index + slides.length) % slides.length;
      track.style.transform = "translateX(-" + current * 100 + "%)";
      if (dotsWrap) {
        dotsWrap.querySelectorAll("button").forEach(function (b, i) {
          b.classList.toggle("active", i === current);
          b.setAttribute("aria-selected", i === current ? "true" : "false");
        });
      }
    }

    function next() {
      goTo(current + 1);
    }

    function startAuto() {
      if (reducedMotion || slides.length < 2) return;
      timer = setInterval(next, 6000);
    }

    function stopAuto() {
      if (timer) clearInterval(timer);
      timer = null;
    }

    slides.forEach(function (_, i) {
      var dot = document.createElement("button");
      dot.setAttribute("role", "tab");
      dot.setAttribute("aria-label", "Show review " + (i + 1));
      dot.addEventListener("click", function () {
        stopAuto();
        goTo(i);
        startAuto();
      });
      if (dotsWrap) dotsWrap.appendChild(dot);
    });
    goTo(0);
    startAuto();

    var slider = document.getElementById("review-slider");
    if (slider) {
      slider.addEventListener("mouseenter", stopAuto);
      slider.addEventListener("mouseleave", startAuto);
    }

    window.matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change", function (m) {
      reducedMotion = m.matches;
      if (reducedMotion) stopAuto();
      else startAuto();
    });
  }

  /* Quote form validation */
  var form = document.getElementById("quote-form");
  var errorLine = document.getElementById("form-error");
  var successLine = document.getElementById("form-success");

  function onlyDigits(str) {
    return str.replace(/\D/g, "");
  }

  function markInvalid(input) {
    input.classList.add("invalid");
    input.addEventListener(
      "input",
      function clear() {
        input.classList.remove("invalid");
        input.removeEventListener("input", clear);
      },
      { once: false }
    );
  }

  function showError(msg) {
    if (errorLine) {
      errorLine.textContent = msg;
      errorLine.classList.add("show");
    }
  }

  function clearError() {
    if (errorLine) {
      errorLine.textContent = "";
      errorLine.classList.remove("show");
    }
  }

  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      clearError();
      if (successLine) successLine.setAttribute("hidden", "");

      var passed = true;
      var name = document.getElementById("q-name");
      var phone = document.getElementById("q-phone");
      var email = document.getElementById("q-email");
      var date = document.getElementById("q-date");
      var party = document.getElementById("q-party");
      var firstBad = null;

      if (!name.value.trim()) { markInvalid(name); passed = false; firstBad = firstBad || name; }
      if (!email.value.trim() || !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email.value.trim())) {
        markInvalid(email); passed = false; firstBad = firstBad || email;
      }
      var phoneDigits = onlyDigits(phone.value);
      if (phoneDigits.length < 10 || phoneDigits.length > 15) {
        markInvalid(phone); passed = false; firstBad = firstBad || phone;
      }
      if (!date.value) { markInvalid(date); passed = false; firstBad = firstBad || date; }
      if (!party.value) { markInvalid(party); passed = false; firstBad = firstBad || party; }

      if (!passed) {
        var phoneBad = onlyDigits(phone.value).length < 10;
        if (phoneBad && phone.classList.contains("invalid")) {
          showError("Please enter a valid 10-digit phone number so we can confirm your table.");
        } else {
          showError("Please complete the required fields marked above.");
        }
        if (firstBad) firstBad.focus();
        return;
      }

      if (successLine) successLine.removeAttribute("hidden");
      form.reset();
    });
  }

  /* Footer year */
  var yearEl = document.getElementById("year");
  if (yearEl) yearEl.textContent = new Date().getFullYear();
})();
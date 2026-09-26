/* ==========================================================================
   90° Bangkok cafe and bar — interactions
   Vanilla JS only. Progressive enhancement — every feature degrades safely.
   ========================================================================== */
(function () {
  "use strict";

  /* Swap the no-js marker so entrance/reveal states only apply when JS runs */
  document.documentElement.classList.remove("no-js");
  document.documentElement.classList.add("js");

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const smoothBehavior = reduceMotion ? "auto" : "smooth";

  /* ------------------------------------------------------------
     Mobile nav toggle
  ------------------------------------------------------------ */
  var navToggle = document.getElementById("nav-toggle");
  var header = document.getElementById("site-header");
  var navLinks = document.getElementById("nav-links");

  function setNav(open) {
    if (!header || !navToggle) return;
    header.classList.toggle("is-open", open);
    navToggle.setAttribute("aria-expanded", open ? "true" : "false");
  }

  if (navToggle) {
    navToggle.addEventListener("click", function () {
      setNav(!header.classList.contains("is-open"));
    });
  }

  /* ------------------------------------------------------------
     Sticky header shadow
  ------------------------------------------------------------ */
  function onScroll() {
    if (header) header.classList.toggle("is-scrolled", window.scrollY > 8);
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  /* ------------------------------------------------------------
     Smooth scroll into view for in-page anchors
  ------------------------------------------------------------ */
  document.querySelectorAll('a[href^="#"]').forEach(function (link) {
    link.addEventListener("click", function (e) {
      var id = link.getAttribute("href");
      if (!id || id.length < 2 || id === "#") return;
      var target = document.getElementById(id.slice(1));
      if (!target) return;
      e.preventDefault();
      setNav(false);
      target.scrollIntoView({ behavior: smoothBehavior, block: "start" });
    });
  });

  /* ------------------------------------------------------------
     Current year in footer
  ------------------------------------------------------------ */
  var yearEl = document.getElementById("year");
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  /* ------------------------------------------------------------
     Scroll reveals (IntersectionObserver)
  ------------------------------------------------------------ */
  var revealEls = Array.prototype.slice.call(document.querySelectorAll(".reveal"));
  if (reduceMotion) {
    revealEls.forEach(function (el) { el.classList.add("is-visible"); });
  } else if ("IntersectionObserver" in window) {
    var revealObserver = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            revealObserver.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15, rootMargin: "0px 0px -40px 0px" }
    );
    revealEls.forEach(function (el) { revealObserver.observe(el); });
  } else {
    revealEls.forEach(function (el) { el.classList.add("is-visible"); });
  }

  /* ------------------------------------------------------------
     Count-up stats
  ------------------------------------------------------------ */
  function animateCount(el) {
    var target = parseFloat(el.getAttribute("data-count-to"));
    var decimals = parseInt(el.getAttribute("data-decimals") || "0", 10);
    var duration = 1300;
    var start = null;
    var wantsReduced = reduceMotion;

    function tick(now) {
      if (start === null) start = now;
      var progress = Math.min((now - start) / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      var value = target * eased;
      el.textContent = value.toFixed(decimals);
      if (progress < 1) requestAnimationFrame(tick);
      else el.textContent = target.toFixed(decimals);
    }

    if (wantsReduced) {
      el.textContent = target.toFixed(decimals);
      return;
    }
    requestAnimationFrame(tick);
  }

  var countEls = Array.prototype.slice.call(document.querySelectorAll("[data-count-to]"));
  if ("IntersectionObserver" in window) {
    var countObserver = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            animateCount(entry.target);
            countObserver.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.4 }
    );
    countEls.forEach(function (el) { countObserver.observe(el); });
  } else {
    countEls.forEach(function (el) {
      el.textContent = parseFloat(el.getAttribute("data-count-to")).toFixed(
        parseInt(el.getAttribute("data-decimals") || "0", 10)
      );
    });
  }

  /* ------------------------------------------------------------
     Hero particle canvas (gold dust)
  ------------------------------------------------------------ */
  var canvas = document.getElementById("hero-canvas");
  var hero = document.getElementById("hero");

  if (canvas && hero && !reduceMotion) {
    var ctx = canvas.getContext("2d");
    if (ctx) {
      var particles = [];
      var raf = null;
      var running = false;
      var w = 0;
      var h = 0;
      var dpr = Math.min(window.devicePixelRatio || 1, 2);

      function resize() {
        var rect = hero.getBoundingClientRect();
        w = rect.width;
        h = rect.height;
        canvas.width = w * dpr;
        canvas.height = h * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }

      function makeParticle(randomY) {
        var base = Math.random() * Math.PI * 2;
        return {
          x: Math.random() * w,
          y: randomY ? Math.random() * h : h + 10,
          r: 0.7 + Math.random() * 1.8,
          vy: 0.08 + Math.random() * 0.28,
          sway: 0.4 + Math.random() * 0.9,
          phase: base,
          twinkle: 0.5 + Math.random() * 0.8,
          pink: Math.random() < 0.18,
          alpha: 0.15 + Math.random() * 0.5
        };
      }

      function init() {
        var count = Math.max(24, Math.min(70, Math.floor(w / 22)));
        particles = [];
        for (var i = 0; i < count; i++) particles.push(makeParticle(true));
      }

      function tick(now) {
        if (!running) return;
        ctx.clearRect(0, 0, w, h);
        var t = now / 1000;
        for (var i = 0; i < particles.length; i++) {
          var p = particles[i];
          p.y -= p.vy;
          p.x += Math.sin(t * 0.9 + p.phase) * p.sway * 0.25;
          if (p.y < -12) {
            particles[i] = makeParticle(false);
            continue;
          }
          var glow = p.alpha * (0.75 + 0.25 * Math.sin(t * p.twinkle + p.phase));
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
          ctx.fillStyle = p.pink
            ? "rgba(255,47,143," + glow + ")"
            : "rgba(255,209,102," + glow + ")";
          ctx.globalCompositeOperation = "lighter";
          ctx.fill();
          ctx.globalCompositeOperation = "source-over";
        }
        raf = requestAnimationFrame(tick);
      }

      function stop() {
        running = false;
        if (raf) cancelAnimationFrame(raf);
        raf = null;
      }

      function start() {
        if (running) return;
        running = true;
        raf = requestAnimationFrame(tick);
      }

      resize();
      init();

      var heroObserver = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) start();
            else stop();
          });
        },
        { threshold: 0 }
      );
      heroObserver.observe(hero);

      var resizeTimer = null;
      window.addEventListener("resize", function () {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(function () {
          resize();
          init();
        }, 180);
      });
    }
  }

  /* ------------------------------------------------------------
     Reviews slider — auto-rotate, tabs (dots), arrows, keyboard
  ------------------------------------------------------------ */
  var slider = document.getElementById("reviews-slider");
  if (slider) {
    var panels = Array.prototype.slice.call(slider.querySelectorAll(".review"));
    var dots = Array.prototype.slice.call(slider.querySelectorAll('[role="tab"]'));
    var tablist = slider.querySelector('[role="tablist"]');
    var prevBtn = document.getElementById("reviews-prev");
    var nextBtn = document.getElementById("reviews-next");
    var current = 0;
    var timer = null;
    var INTERVAL = 6000;

    function show(index, moveFocus) {
      current = (index + panels.length) % panels.length;
      var activeId = "review-panel-" + (current + 1);
      panels.forEach(function (panel) {
        var isActive = panel.id === activeId;
        panel.classList.toggle("is-active", isActive);
        panel.hidden = !isActive;
      });
      dots.forEach(function (dot, i) {
        var isActive = i === current;
        dot.classList.toggle("is-active", isActive);
        dot.setAttribute("aria-selected", isActive ? "true" : "false");
        dot.tabIndex = isActive ? 0 : -1;
        if (moveFocus && isActive) dot.focus();
      });
    }

    function stopAuto() {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
    }

    function startAuto() {
      if (!timer) {
        timer = setInterval(function () { show(current + 1, false); }, INTERVAL);
      }
    }

    function resetAuto() {
      stopAuto();
      startAuto();
    }

    if (dots.length && panels.length) {
      slider.addEventListener("mouseenter", stopAuto);
      slider.addEventListener("mouseleave", startAuto);
      slider.addEventListener("focusin", stopAuto);
      slider.addEventListener("focusout", startAuto);

      dots.forEach(function (dot, i) {
        dot.addEventListener("click", function () {
          show(i, false);
          resetAuto();
        });
      });

      if (prevBtn) {
        prevBtn.addEventListener("click", function () {
          show(current - 1, false);
          resetAuto();
        });
      }
      if (nextBtn) {
        nextBtn.addEventListener("click", function () {
          show(current + 1, false);
          resetAuto();
        });
      }

      if (tablist) {
        tablist.addEventListener("keydown", function (e) {
          var key = e.key;
          if (["ArrowRight", "ArrowLeft", "Home", "End"].indexOf(key) === -1) return;
          e.preventDefault();
          if (key === "ArrowRight") show(current + 1, true);
          else if (key === "ArrowLeft") show(current - 1, true);
          else if (key === "Home") show(0, true);
          else if (key === "End") show(panels.length - 1, true);
          resetAuto();
        });
      }

      startAuto();
    }
  }

  /* ------------------------------------------------------------
     Quote / reservation form — validation + inline success
  ------------------------------------------------------------ */
  var form = document.getElementById("quote-form");
  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();

      var name = document.getElementById("f-name");
      var phone = document.getElementById("f-phone");
      var date = document.getElementById("f-date");
      var guests = document.getElementById("f-guests");
      var email = document.getElementById("f-email");

      var valid = true;

      function fail(field, msg) {
        var err = document.getElementById(field.id + "-error");
        field.setAttribute("aria-invalid", "true");
        if (err) err.textContent = msg;
        field.focus();
        valid = false;
      }

      function pass(field) {
        field.removeAttribute("aria-invalid");
        var err = document.getElementById(field.id + "-error");
        if (err) err.textContent = "";
      }

      // name — required
      if (!name.value.trim() || name.value.trim().length < 2) {
        fail(name, "Please enter your name.");
      } else {
        pass(name);
      }

      // phone — required, 10–15 digits after scrubbing formatting
      var digits = (phone.value || "").replace(/\D/g, "");
      if (!digits) {
        fail(phone, "Please enter your phone number.");
      } else if (digits.length < 10 || digits.length > 15) {
        fail(phone, "That phone number looks too " + (digits.length < 10 ? "short" : "long") + ". Use 10 or more digits.");
      } else {
        pass(phone);
      }

      // date — required
      if (!date.value) {
        fail(date, "Please choose a date.");
      } else {
        pass(date);
      }

      // guests — required
      if (!guests.value) {
        fail(guests, "Please pick a party size.");
      } else {
        pass(guests);
      }

      // email — optional but validated when present
      if (email && email.value.trim()) {
        var emailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value.trim());
        if (!emailValid) {
          fail(email, "That email address doesn't look right.");
        } else {
          pass(email);
        }
      }

      if (!valid) return;

      // Success — demo preview does not actually contact the business
      form.hidden = true;
      var success = document.getElementById("form-success");
      if (success) {
        success.hidden = false;
        success.scrollIntoView({ behavior: smoothBehavior, block: "center" });
      }
    });

    // clear inline errors as the visitor corrects each field
    ["f-name", "f-phone", "f-date", "f-guests", "f-email"].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.addEventListener("input", function () { el.removeAttribute("aria-invalid"); });
      el.addEventListener("change", function () {
        var err = document.getElementById(el.id + "-error");
        if (err) err.textContent = "";
      });
    });
  }
})();
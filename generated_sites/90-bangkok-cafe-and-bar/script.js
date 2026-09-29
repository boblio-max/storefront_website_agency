document.body.classList.add('js');
/* ============================================================
   90° Bangkok cafe and bar — interaction layer
   Vanilla, no dependencies. Transform/opacity animation only,
   driven by requestAnimationFrame with passive listeners and
   paused when off-screen. Everything stops under
   prefers-reduced-motion.
   ============================================================ */
(function () {
  'use strict';

  /* ---------- flags ---------- */
  var REDUCED = false;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    REDUCED = true; /* skip auto-rotate, marquee drift, magnetic pull, aurora loop */
  }
  var FINE_POINTER = window.matchMedia('(hover: hover) and (pointer: fine)').matches;

  /* ---------- tiny helpers ---------- */
  function qa(sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); }
  function q(sel) { return document.querySelector(sel); }
  function on(el, type, fn, opts) { if (el) { el.addEventListener(type, fn, opts || false); } }

  /* ============================================================
     1 · footer year
     ============================================================ */
  var year = q('#year');
  if (year) { year.textContent = String(new Date().getFullYear()); }

  /* ============================================================
     2 · sticky header shadow
     ============================================================ */
  var header = q('#site-header');
  var lastY = -1;
  var scrollQueued = false;
  function onScroll() {
    if (scrollQueued) { return; }
    scrollQueued = true;
    requestAnimationFrame(function () {
      scrollQueued = false;
      var y = window.pageYOffset || document.documentElement.scrollTop || 0;
      if (y !== lastY) {
        lastY = y;
        if (header) { header.classList.toggle('is-stuck', y > 12); }
      }
    });
  }
  on(window, 'scroll', onScroll, { passive: true });
  onScroll();

  /* ============================================================
     3 · in-page anchors (offset for the sticky header)
     ============================================================ */
  var navToggle = q('.nav-toggle');
  var siteHeader = q('.site-header');
  function closeNav() {
    if (!siteHeader || !navToggle) { return; }
    siteHeader.classList.remove('nav-open');
    navToggle.setAttribute('aria-expanded', 'false');
  }
  qa('a[href^="#"]').forEach(function (link) {
    on(link, 'click', function (e) {
      var hash = link.getAttribute('href');
      if (!hash || hash === '#') { return; }
      var target = document.getElementById(hash.slice(1));
      if (!target) { return; }
      e.preventDefault();
      closeNav();
      var offset = (header ? header.offsetHeight : 0) + 14;
      var top = target.getBoundingClientRect().top + (window.pageYOffset || 0) - offset;
      window.scrollTo({ top: Math.max(top, 0), behavior: REDUCED ? 'auto' : 'smooth' });
      if (window.history && window.history.replaceState) { window.history.replaceState(null, '', hash); }
      if (e.detail === 0) { /* keyboard activation — move focus to the destination */
        target.setAttribute('tabindex', '-1');
        target.focus({ preventScroll: true });
      }
    });
  });

  /* ============================================================
     4 · mobile nav toggle
     ============================================================ */
  if (navToggle && siteHeader) {
    on(navToggle, 'click', function () {
      var open = siteHeader.classList.toggle('nav-open');
      navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    on(document, 'keydown', function (e) {
      if (e.key === 'Escape' && siteHeader.classList.contains('nav-open')) {
        closeNav();
        navToggle.focus();
      }
    });
    on(window, 'resize', function () {
      if (window.innerWidth > 760) { closeNav(); }
    });
  }

  /* ============================================================
     5 · scroll reveals
     ============================================================ */
  var revealables = qa('.reveal');
  if (revealables.length) {
    if (!REDUCED && 'IntersectionObserver' in window) {
      var revealIO = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-in');
            revealIO.unobserve(entry.target);
          }
        });
      }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });
      revealables.forEach(function (el) { revealIO.observe(el); });
    } else {
      revealables.forEach(function (el) { el.classList.add('is-in'); });
    }
  }

  /* ============================================================
     6 · count-up stats
     ============================================================ */
  var counters = qa('[data-count]');
  function runCount(el) {
    var target = parseFloat(el.getAttribute('data-count'));
    var decimals = parseInt(el.getAttribute('data-decimals') || '0', 10);
    if (isNaN(target)) { return; }
    if (REDUCED) { el.textContent = target.toFixed(decimals); return; }
    var duration = 1600;
    var start = null;
    function frame(ts) {
      if (start === null) { start = ts; }
      var p = Math.min((ts - start) / duration, 1);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = (target * eased).toFixed(decimals);
      if (p < 1) { requestAnimationFrame(frame); }
    }
    requestAnimationFrame(frame);
  }
  if (counters.length) {
    if ('IntersectionObserver' in window) {
      var countIO = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            runCount(entry.target);
            countIO.unobserve(entry.target);
          }
        });
      }, { threshold: 0.6 });
      counters.forEach(function (el) { countIO.observe(el); });
    } else {
      counters.forEach(runCount);
    }
  }

  /* ============================================================
     7 · hero aurora canvas — warm incense bloom, rendered at
         half resolution and upscaled (cheaper, and gradients
         hide the resample)
     ============================================================ */
  var auroraPlay = function () {};
  var auroraHalt = function () {};
  var canvas = q('#heroAurora');
  if (canvas && canvas.getContext) {
    var ctx = canvas.getContext('2d');
    var blobs = [
      { ox: 0.22, oy: 0.30, r: 0.52, sx: 0.00019, sy: 0.00023, ph: 0.00, c: '217,164,65', a: 0.30 },
      { ox: 0.72, oy: 0.20, r: 0.46, sx: 0.00026, sy: 0.00015, ph: 1.70, c: '255,196,120', a: 0.22 },
      { ox: 0.52, oy: 0.76, r: 0.58, sx: 0.00014, sy: 0.00021, ph: 3.10, c: '201,74,48', a: 0.26 },
      { ox: 0.88, oy: 0.62, r: 0.40, sx: 0.00031, sy: 0.00018, ph: 4.60, c: '217,164,65', a: 0.18 },
      { ox: 0.10, oy: 0.86, r: 0.44, sx: 0.00017, sy: 0.00027, ph: 5.90, c: '139,26,16', a: 0.30 }
    ];
    var W = 0, H = 0, T = 0, rafId = null, lastTs = null, onScreen = true, visible = true;

    function sizeCanvas() {
      var rect = canvas.getBoundingClientRect();
      W = Math.max(1, Math.round(rect.width * 0.5));
      H = Math.max(1, Math.round(rect.height * 0.5));
      canvas.width = W;
      canvas.height = H;
    }

    function paint() {
      ctx.clearRect(0, 0, W, H);
      ctx.globalCompositeOperation = 'lighter';
      for (var i = 0; i < blobs.length; i++) {
        var b = blobs[i];
        var bx = (b.ox + 0.13 * Math.sin(T * b.sx + b.ph)) * W;
        var by = (b.oy + 0.11 * Math.cos(T * b.sy + b.ph)) * H;
        var br = b.r * Math.min(W, H) * (1 + 0.08 * Math.sin(T * b.sx * 0.6 + b.ph));
        var g = ctx.createRadialGradient(bx, by, 0, bx, by, br);
        g.addColorStop(0, 'rgba(' + b.c + ',' + b.a + ')');
        g.addColorStop(0.42, 'rgba(' + b.c + ',' + (b.a * 0.26).toFixed(3) + ')');
        g.addColorStop(1, 'rgba(' + b.c + ',0)');
        ctx.fillStyle = g;
        ctx.fillRect(bx - br, by - br, br * 2, br * 2);
      }
      ctx.globalCompositeOperation = 'source-over';
    }

    function tick(ts) {
      rafId = null;
      if (lastTs === null) { lastTs = ts; }
      var dt = Math.min(ts - lastTs, 48);
      lastTs = ts;
      T += dt;
      paint();
      if (onScreen && visible && !REDUCED) { rafId = requestAnimationFrame(tick); }
    }

    function play() {
      if (rafId === null && onScreen && visible && !REDUCED) { rafId = requestAnimationFrame(tick); }
    }
    function halt() {
      if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
    }
    auroraPlay = play;
    auroraHalt = halt;

    sizeCanvas();
    T = REDUCED ? 1800 : 0;
    paint();
    play();

    if (!REDUCED) {
      var resizeTimer = null;
      on(window, 'resize', function () {
        if (resizeTimer) { clearTimeout(resizeTimer); }
        resizeTimer = setTimeout(function () { sizeCanvas(); paint(); }, 180);
      });
      on(document, 'visibilitychange', function () {
        visible = !document.hidden;
        if (visible) { play(); } else { halt(); }
      });
      if ('IntersectionObserver' in window) {
        var heroIO = new IntersectionObserver(function (entries) {
          onScreen = entries[0].isIntersecting;
          if (onScreen) { play(); } else { halt(); }
        }, { threshold: 0 });
        heroIO.observe(canvas);
      }
    }
  }

  /* ============================================================
     8 · magnetic primary buttons (fine pointers only)
     ============================================================ */
  if (!REDUCED && FINE_POINTER) {
    qa('[data-magnetic]').forEach(function (btn) {
      var pending = null, tx = 0, ty = 0;
      function apply() {
        pending = null;
        btn.style.transform = 'translate3d(' + tx.toFixed(2) + 'px,' + ty.toFixed(2) + 'px,0)';
      }
      on(btn, 'pointermove', function (e) {
        var r = btn.getBoundingClientRect();
        tx = ((e.clientX - r.left) / r.width - 0.5) * 10;
        ty = ((e.clientY - r.top) / r.height - 0.5) * 8;
        if (pending === null) { pending = requestAnimationFrame(apply); }
      });
      on(btn, 'pointerleave', function () {
        tx = 0; ty = 0;
        if (pending === null) { pending = requestAnimationFrame(apply); }
      });
      on(btn, 'blur', function () { tx = 0; ty = 0; btn.style.transform = ''; });
    });
  }

  /* ============================================================
     9 · review slider — tabs, auto-rotate, swipe
     ============================================================ */
  var dots = qa('.review-dot');
  var panels = qa('.review-panel');
  var slideWindow = q('#review-window');
  var slidePlay = function () {};
  var slideHalt = function () {};

  if (dots.length && panels.length && slideWindow) {
    var idx = 0;
    var timer = null;
    var engaged = false;
    var DELAY = 6500;

    function show(next, moveFocus) {
      idx = (next + panels.length) % panels.length;
      panels.forEach(function (panel, i) {
        var active = i === idx;
        panel.hidden = !active;
        panel.classList.toggle('is-active', active);
      });
      dots.forEach(function (dot, i) {
        dot.setAttribute('aria-selected', i === idx ? 'true' : 'false');
        dot.tabIndex = i === idx ? 0 : -1;
      });
      if (moveFocus) { dots[idx].focus(); }
    }

    function play() {
      if (REDUCED || engaged || timer !== null) { return; }
      timer = setInterval(function () { show(idx + 1, false); }, DELAY);
    }
    function halt() {
      if (timer !== null) { clearInterval(timer); timer = null; }
    }
    slidePlay = play;
    slideHalt = halt;

    dots.forEach(function (dot, i) {
      on(dot, 'click', function () { engaged = true; halt(); show(i, false); });
      on(dot, 'keydown', function (e) {
        var target = null;
        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { target = idx + 1; }
        else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { target = idx - 1; }
        else if (e.key === 'Home') { target = 0; }
        else if (e.key === 'End') { target = panels.length - 1; }
        if (target === null) { return; }
        e.preventDefault();
        show(target, true);
      });
    });

    on(slideWindow, 'mouseenter', halt);
    on(slideWindow, 'mouseleave', function () { if (!engaged) { play(); } });
    on(slideWindow, 'focusin', halt);
    on(slideWindow, 'focusout', function () { if (!engaged) { play(); } });

    /* touch swipe */
    var startX = 0, startY = 0, tracking = false;
    on(slideWindow, 'touchstart', function (e) {
      if (e.touches.length !== 1) { tracking = false; return; }
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      tracking = true;
      halt();
    }, { passive: true });
    on(slideWindow, 'touchend', function (e) {
      if (!tracking) { return; }
      tracking = false;
      var t = e.changedTouches[0];
      var dx = t.clientX - startX;
      var dy = t.clientY - startY;
      if (Math.abs(dx) > 44 && Math.abs(dx) > Math.abs(dy)) {
        show(idx + (dx < 0 ? 1 : -1), false);
      } else if (!engaged) { play(); }
    }, { passive: true });

    if ('IntersectionObserver' in window) {
      var slideIO = new IntersectionObserver(function (entries) {
        if (entries[0].isIntersecting) { play(); } else { halt(); }
      }, { threshold: 0.25 });
      slideIO.observe(slideWindow);
    } else {
      play();
    }
  }

  /* ============================================================
     10 · reservation request form
     ============================================================ */
  var form = q('#quote-form');
  if (form) {
    var status = q('#form-status');
    var rules = [
      { id: 'q-name', msg: 'Add the name the table is under.', test: function (v) { return v.trim().length >= 2; } },
      { id: 'q-phone', msg: 'Enter a 10-digit phone number, e.g. (425) 481-6800.', test: function (v) { return v.replace(/\D/g, '').length >= 10; } },
      { id: 'q-email', msg: 'Add a valid email so the room can confirm.', test: function (v) { return /^[^\s@]+@[^\s@]+\.[a-z]{2,}$/i.test(v.trim()); } },
      { id: 'q-date', msg: 'Pick a date for the request.', test: function (v) { return v.length > 0; } }
    ];
    var submitted = false;

    function fieldOf(input) { return input.closest('.field'); }
    function mark(input, message) {
      var wrap = fieldOf(input);
      if (wrap) { wrap.classList.toggle('is-bad', message.length > 0); }
      input.setAttribute('aria-invalid', message.length > 0 ? 'true' : 'false');
      var slot = document.getElementById(input.id + '-err');
      if (slot) { slot.textContent = message; }
      return message.length === 0;
    }
    function check(input) {
      var rule = null;
      for (var i = 0; i < rules.length; i++) { if (rules[i].id === input.id) { rule = rules[i]; } }
      if (!rule) { return true; }
      return mark(input, rule.test(input.value) ? '' : rule.msg);
    }

    rules.forEach(function (rule) {
      var input = document.getElementById(rule.id);
      if (!input) { return; }
      on(input, 'blur', function () { if (submitted || input.value.length) { check(input); } });
      on(input, 'input', function () { if (fieldOf(input) && fieldOf(input).classList.contains('is-bad')) { check(input); } });
    });

    on(form, 'submit', function (e) {
      e.preventDefault();
      submitted = true;
      var firstBad = null;
      rules.forEach(function (rule) {
        var input = document.getElementById(rule.id);
        if (!input) { return; }
        if (!check(input) && !firstBad) { firstBad = input; }
      });
      if (firstBad) {
        if (status) {
          status.classList.remove('is-ok');
          status.textContent = 'Almost — check the highlighted fields and send it again.';
        }
        firstBad.focus();
        return;
      }
      if (status) {
        status.classList.add('is-ok');
        status.textContent = 'Thanks! (Demo preview — this form goes live when the site launches.)';
      }
    });
  }

  /* ============================================================
     11 · honour a mid-session change of motion preference
     ============================================================ */
  if (window.matchMedia) {
    var motionMQ = window.matchMedia('(prefers-reduced-motion: reduce)');
    var onMotionChange = function (e) {
      if (e.matches) {
        REDUCED = true;
        auroraHalt();
        slideHalt();
        qa('.marquee__track').forEach(function (track) { track.style.animation = 'none'; });
        counters.forEach(function (el) {
          if (!el.getAttribute('data-done')) {
            el.setAttribute('data-done', '1');
            runCount(el);
          }
        });
      } else {
        REDUCED = false;
      }
    };
    if (typeof motionMQ.addEventListener === 'function') {
      motionMQ.addEventListener('change', onMotionChange);
    } else if (typeof motionMQ.addListener === 'function') {
      motionMQ.addListener(onMotionChange);
    }
  }
})();

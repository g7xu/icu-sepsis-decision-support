/**
 * ICU Sepsis Decision Support System
 * Website scripts – smooth scroll, nav behavior
 */

(function () {
  'use strict';

  // Update nav CTA href when you have the product URL
  const PRODUCT_URL = '/patients/';

  function initThemeToggle() {
    const toggle = document.getElementById('theme-toggle');
    if (!toggle) return;

    toggle.addEventListener('click', () => {
      const html = document.documentElement;
      const current = html.getAttribute('data-theme');
      const next = current === 'dark' ? 'light' : 'dark';
      html.setAttribute('data-theme', next);
      localStorage.setItem('theme', next);
    });
  }

  function initNav() {
    const nav = document.getElementById('nav');
    const navToggle = document.getElementById('nav-toggle');
    const navLinks = document.querySelectorAll('.nav-links a');

    // Mobile menu toggle
    if (navToggle) {
      navToggle.addEventListener('click', () => {
        nav.classList.toggle('open');
      });
    }

    // Close mobile menu when a link is clicked
    navLinks.forEach(link => {
      link.addEventListener('click', () => {
        nav.classList.remove('open');
      });
    });

    // Set product URL on CTA buttons/links that still have placeholder href
    document.querySelectorAll('a.nav-cta, .btn-primary').forEach(el => {
      if (PRODUCT_URL !== '#' && el.getAttribute('href') === '#') {
        el.href = PRODUCT_URL;
      }
    });
  }

  // Scrollytelling: fade-in as elements enter viewport, fade-out when they leave
  function initScrollReveal() {
    const reveals = document.querySelectorAll('.scroll-reveal');
    if (!reveals.length) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('revealed');
          } else {
            entry.target.classList.remove('revealed');
          }
        });
      },
      {
        root: null,
        rootMargin: '0px 0px -5% 0px',
        threshold: 0
      }
    );

    reveals.forEach((el) => observer.observe(el));
  }

  // Image lightbox: click expandable images to view full size
  function initImageLightbox() {
    const lightbox = document.getElementById('lightbox');
    const lightboxImg = document.getElementById('lightbox-img');
    const backdrop = lightbox?.querySelector('.lightbox__backdrop');
    const closeBtn = lightbox?.querySelector('.lightbox__close');
    const expandableImages = document.querySelectorAll('.img-expandable');

    if (!lightbox || !lightboxImg) return;

    function openLightbox(src, alt) {
      lightboxImg.src = src;
      lightboxImg.alt = alt;
      lightbox.classList.add('is-open');
      lightbox.removeAttribute('hidden');
      document.body.style.overflow = 'hidden';
    }

    function closeLightbox() {
      lightbox.classList.remove('is-open');
      lightbox.setAttribute('hidden', '');
      document.body.style.overflow = '';
    }

    expandableImages.forEach((img) => {
      img.addEventListener('click', (e) => {
        e.preventDefault();
        openLightbox(img.src, img.alt);
      });
    });

    if (backdrop) backdrop.addEventListener('click', closeLightbox);
    if (closeBtn) closeBtn.addEventListener('click', closeLightbox);

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && lightbox.classList.contains('is-open')) {
        closeLightbox();
      }
    });
  }

  // Highlight nav link for current section on scroll
  function initScrollHighlight() {
    const sections = document.querySelectorAll('section[id]');
    const navLinks = document.querySelectorAll('.nav-links a[href^="#"]');

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            const id = entry.target.getAttribute('id');
            navLinks.forEach((link) => {
              link.classList.toggle('active', link.getAttribute('href') === `#${id}`);
            });
          }
        });
      },
      { rootMargin: '-20% 0px -60% 0px', threshold: 0 }
    );

    sections.forEach((section) => observer.observe(section));
  }

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      initThemeToggle();
      initNav();
      initScrollReveal();
      initImageLightbox();
      initScrollHighlight();
    });
  } else {
    initThemeToggle();
    initNav();
    initScrollReveal();
    initImageLightbox();
    initScrollHighlight();
  }
})();

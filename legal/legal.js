/* Ungles i Estètica - páginas legales: selector de idioma (es, ca, en, uk) */
(function () {
  var LANGS = ['es', 'ca', 'en', 'uk'];
  var root = document.documentElement;

  function valid(l) { return LANGS.indexOf(l) !== -1 ? l : null; }
  function stored() { try { return valid(localStorage.getItem('lang')); } catch (e) { return null; } }
  function fromQuery() {
    var m = /[?&]lang=([a-zA-Z-]+)/.exec(location.search);
    return m ? valid(m[1].toLowerCase().slice(0, 2)) : null;
  }

  function apply(lang, save) {
    root.lang = lang;
    root.setAttribute('data-active', lang);
    if (save) { try { localStorage.setItem('lang', lang); } catch (e) {} }
    var btns = document.querySelectorAll('[data-set-lang]');
    for (var i = 0; i < btns.length; i++) {
      btns[i].setAttribute('aria-pressed', btns[i].getAttribute('data-set-lang') === lang ? 'true' : 'false');
    }
    var h1 = document.querySelector('section[data-lang="' + lang + '"] h1');
    if (h1) { document.title = h1.textContent + ' · Ungles i Estètica'; }
  }

  var q = fromQuery();
  var current = q || stored() || 'es';
  root.className += (root.className ? ' ' : '') + 'js';
  apply(current, !!q);

  document.addEventListener('DOMContentLoaded', function () {
    apply(current, false);
    var btns = document.querySelectorAll('[data-set-lang]');
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener('click', function () {
        current = this.getAttribute('data-set-lang');
        apply(current, true);
      });
    }
  });
})();

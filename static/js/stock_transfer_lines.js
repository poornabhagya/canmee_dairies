/**
 * Dynamic inline rows for stock transfer formset + Select2 product pickers.
 */
(function () {
  'use strict';

  function $all(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  function getTotalFormsInput() {
    return document.getElementById('id_form-TOTAL_FORMS');
  }

  function renumberTransferLines() {
    var tbody = document.getElementById('js-transfer-lines-body');
    if (!tbody) return;
    var rows = $all('.js-transfer-line', tbody);
    rows.forEach(function (row, i) {
      $all('[name]', row).forEach(function (el) {
        el.name = el.name.replace(/form-\d+-/, 'form-' + i + '-');
      });
      $all('[id]', row).forEach(function (el) {
        if (!el.id) return;
        if (/^id_form-\d+-/.test(el.id)) {
          el.id = el.id.replace(/^id_form-\d+-/, 'id_form-' + i + '-');
        } else if (/^id_form_\d+_/.test(el.id)) {
          el.id = el.id.replace(/^id_form_\d+_/, 'id_form_' + i + '_');
        }
      });
      var numEl = row.querySelector('.js-line-num');
      if (numEl) numEl.textContent = String(i + 1);
    });
    var totalInput = getTotalFormsInput();
    if (totalInput) totalInput.value = String(rows.length);
  }

  function notifyLinesChanged() {
    document.dispatchEvent(new CustomEvent('transfer-lines-changed'));
  }

  function destroyAllProductSelect2() {
    if (!window.jQuery || !window.jQuery.fn.select2) return;
    window.jQuery('select.js-transfer-product-select').each(function () {
      var $el = window.jQuery(this);
      if ($el.hasClass('select2-hidden-accessible')) {
        $el.select2('destroy');
      }
    });
  }

  function initAllProductSelect2() {
    if (!window.jQuery || !window.jQuery.fn.select2) return;
    window.jQuery('select.js-transfer-product-select').each(function () {
      var $el = window.jQuery(this);
      if ($el.hasClass('select2-hidden-accessible')) return;
      $el.select2({
        width: '100%',
        placeholder: 'Search product…',
        allowClear: true,
        dropdownAutoWidth: false,
      });
    });
  }

  function addLine() {
    var tbody = document.getElementById('js-transfer-lines-body');
    if (!tbody) return;
    var proto = tbody.querySelector('.js-transfer-line');
    if (!proto) return;
    var maxInput = document.getElementById('id_form-MAX_NUM_FORMS');
    var maxNum = maxInput ? parseInt(maxInput.value, 10) : 200;
    var current = tbody.querySelectorAll('.js-transfer-line').length;
    if (current >= maxNum) return;

    destroyAllProductSelect2();
    var row = proto.cloneNode(true);
    row.querySelectorAll('select').forEach(function (s) {
      s.selectedIndex = 0;
    });
    row.querySelectorAll('input').forEach(function (inp) {
      inp.value = '';
    });
    tbody.appendChild(row);
    renumberTransferLines();
    initAllProductSelect2();
    notifyLinesChanged();
    var rowsAfter = tbody.querySelectorAll('.js-transfer-line');
    var lastRow = rowsAfter[rowsAfter.length - 1];
    if (lastRow) {
      var ps = lastRow.querySelector('select.js-transfer-product-select');
      if (ps) {
        ps.focus();
        if (window.jQuery && window.jQuery(ps).hasClass('select2-hidden-accessible')) {
          window.jQuery(ps).select2('open');
        }
      }
    }
  }

  function removeLine(btn) {
    var tbody = document.getElementById('js-transfer-lines-body');
    if (!tbody) return;
    var row = btn.closest('.js-transfer-line');
    if (!row) return;
    var rows = tbody.querySelectorAll('.js-transfer-line');
    if (rows.length <= 1) {
      row.querySelectorAll('select').forEach(function (s) {
        s.selectedIndex = 0;
      });
      row.querySelectorAll('input').forEach(function (inp) {
        inp.value = '';
      });
      destroyAllProductSelect2();
      initAllProductSelect2();
      notifyLinesChanged();
      return;
    }
    destroyAllProductSelect2();
    row.parentNode.removeChild(row);
    renumberTransferLines();
    initAllProductSelect2();
    notifyLinesChanged();
  }

  function focusQtyInRowFromProduct(productSelect) {
    var row = productSelect.closest('.js-transfer-line');
    if (!row) return;
    var qty = row.querySelector('input.transfer-qty-input, input.js-transfer-qty');
    if (!qty) return;
    qty.focus();
    if (typeof qty.select === 'function') qty.select();
  }

  function bindProductSelectToQty(form) {
    form.addEventListener('change', function (e) {
      var t = e.target;
      if (t.tagName === 'SELECT' && t.classList.contains('js-transfer-product-select') && t.value) {
        focusQtyInRowFromProduct(t);
      }
    });
    if (!window.jQuery || !window.jQuery.fn.select2) return;
    window.jQuery(form).on('select2:select', 'select.js-transfer-product-select', function () {
      focusQtyInRowFromProduct(this);
    });
  }

  function getOrderedFocusables(form) {
    var all = form.querySelectorAll('input, select, textarea, button, a[href]');
    var out = [];
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.type === 'hidden') continue;
      if (el.disabled) continue;
      if (el.tabIndex < 0 && el.tagName !== 'SELECT') continue;
      var cs = window.getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      var r = el.getBoundingClientRect();
      if (el.tagName !== 'SELECT' && (r.width < 1 || r.height < 1)) continue;
      out.push(el);
    }
    return out;
  }

  function bindEnterNavigation(form) {
    form.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' || e.ctrlKey || e.altKey || e.metaKey) return;
      if (e.target.closest && e.target.closest('.select2-dropdown')) return;
      if (e.target.tagName === 'TEXTAREA') return;
      if (e.target.matches && e.target.matches('button[type="submit"], input[type="submit"]')) return;
      if (e.target.closest && e.target.closest('button[type="submit"]')) return;
      if (e.target.tagName === 'BUTTON' && e.target.type === 'button') return;

      var t = e.target;
      var tbody = document.getElementById('js-transfer-lines-body');

      if (tbody && t.closest && t.closest('.js-transfer-line')) {
        if (t.matches && t.matches('input.transfer-qty-input, input.js-transfer-qty')) {
          e.preventDefault();
          var row = t.closest('.js-transfer-line');
          var rows = tbody.querySelectorAll('.js-transfer-line');
          var idx = Array.prototype.indexOf.call(rows, row);
          var nextRow = rows[idx + 1];
          if (nextRow) {
            var ps = nextRow.querySelector('select.js-transfer-product-select');
            if (ps) {
              ps.focus();
              if (window.jQuery && window.jQuery(ps).hasClass('select2-hidden-accessible')) {
                window.jQuery(ps).select2('open');
              }
            }
          } else {
            var addBtn = document.getElementById('js-transfer-add-line');
            if (addBtn) addBtn.focus();
          }
          return;
        }
      }

      var focusables = getOrderedFocusables(form);
      var fi = focusables.indexOf(t);
      if (fi === -1) return;

      e.preventDefault();
      var next = focusables[fi + 1];
      if (!next) return;
      next.focus();
      if (next.tagName === 'SELECT' && window.jQuery && window.jQuery(next).hasClass('select2-hidden-accessible')) {
        setTimeout(function () {
          window.jQuery(next).select2('open');
        }, 10);
      }
      if (next.tagName === 'INPUT' && typeof next.select === 'function' && next.type !== 'checkbox' && next.type !== 'radio') {
        next.select();
      }
    });
  }

  function bind() {
    var form = document.querySelector('form.js-transfer-form');
    var tbody = document.getElementById('js-transfer-lines-body');
    if (!form || !tbody) return;

    var addBtn = document.getElementById('js-transfer-add-line');
    if (addBtn) {
      addBtn.addEventListener('click', function (e) {
        e.preventDefault();
        addLine();
      });
    }

    tbody.addEventListener('click', function (e) {
      var t = e.target;
      if (t.closest && t.closest('.js-transfer-remove-line')) {
        e.preventDefault();
        removeLine(t.closest('.js-transfer-remove-line'));
      }
    });

    form.addEventListener('submit', function () {
      destroyAllProductSelect2();
      renumberTransferLines();
    });

    bindProductSelectToQty(form);
    bindEnterNavigation(form);

    renumberTransferLines();
    initAllProductSelect2();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();

/**
 * GRN line formset: inline table rows, renumbering (prefix items), Select2 product pickers.
 */
(function () {
  'use strict';

  function $all(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  function notifyChanged() {
    document.dispatchEvent(new CustomEvent('grn-lines-changed'));
  }

  function getTotalFormsInput() {
    return document.querySelector('input[name="items-TOTAL_FORMS"]');
  }

  function destroyGrnProductSelect2In(root) {
    if (!window.jQuery || !window.jQuery.fn.select2) return;
    var $scope = root ? window.jQuery(root) : window.jQuery(document);
    $scope.find('select.js-grn-product-select').each(function () {
      var $el = window.jQuery(this);
      if ($el.hasClass('select2-hidden-accessible')) {
        $el.select2('destroy');
      }
    });
  }

  function initGrnProductSelect2() {
    if (!window.jQuery || !window.jQuery.fn.select2) return;
    window.jQuery('select.js-grn-product-select').each(function () {
      var $el = window.jQuery(this);
      if ($el.hasClass('select2-hidden-accessible')) return;
      if ($el.closest('tr.d-none').length) return;
      $el.select2({
        width: '100%',
        placeholder: 'Search product…',
        allowClear: true,
        dropdownAutoWidth: false,
      });
    });
  }

  function renumberGrnLines() {
    var tbody = document.getElementById('js-grn-lines-body');
    if (!tbody) return;
    var rows = $all('tr.js-grn-line', tbody);
    rows.forEach(function (row, i) {
      $all('[name]', row).forEach(function (el) {
        var name = el.getAttribute('name');
        if (!name || name.indexOf('items-') !== 0) return;
        el.setAttribute('name', name.replace(/items-\d+-/, 'items-' + i + '-'));
      });
      $all('[id]', row).forEach(function (el) {
        var id = el.id;
        if (!id) return;
        if (/^id_items-\d+-/.test(id)) {
          el.id = id.replace(/^id_items-\d+-/, 'id_items-' + i + '-');
        }
      });
      var numEl = row.querySelector('.js-line-num');
      if (numEl) numEl.textContent = String(i + 1);
    });
    var totalInput = getTotalFormsInput();
    if (totalInput) totalInput.value = String(rows.length);
  }

  function clearRowValues(row) {
    row.querySelectorAll('select').forEach(function (s) {
      s.selectedIndex = 0;
    });
    row.querySelectorAll('input').forEach(function (inp) {
      var name = inp.name || '';
      if (name.indexOf('-DELETE') !== -1) {
        inp.checked = false;
        return;
      }
      if (/items-\d+-id$/.test(name)) {
        inp.value = '';
        return;
      }
      if (inp.type === 'checkbox') inp.checked = false;
      else inp.value = '';
    });
  }

  function addLine() {
    var tbody = document.getElementById('js-grn-lines-body');
    if (!tbody) return;
    var proto =
      tbody.querySelector('.js-grn-line:not(.d-none)') || tbody.querySelector('.js-grn-line');
    if (!proto) return;
    var maxInput = document.querySelector('input[name="items-MAX_NUM_FORMS"]');
    var maxNum = maxInput ? parseInt(maxInput.value, 10) : 200;
    var current = tbody.querySelectorAll('tr.js-grn-line').length;
    if (current >= maxNum) return;

    destroyGrnProductSelect2In(document);
    var row = proto.cloneNode(true);
    row.classList.remove('d-none', 'grn-line-muted');
    clearRowValues(row);
    tbody.appendChild(row);
    renumberGrnLines();
    initGrnProductSelect2();
    notifyChanged();
  }

  function removeLine(btn) {
    var tbody = document.getElementById('js-grn-lines-body');
    if (!tbody) return;
    var row = btn.closest('tr.js-grn-line');
    if (!row) return;

    var idInp = row.querySelector('input[name$="-id"]');
    var hasSavedId = idInp && idInp.value && String(idInp.value).trim() !== '';

    if (hasSavedId) {
      var del = row.querySelector('input[name$="-DELETE"]');
      if (del) del.checked = true;
      row.classList.add('grn-line-muted');
      row.classList.add('d-none');
      destroyGrnProductSelect2In(row);
      renumberGrnLines();
      initGrnProductSelect2();
      notifyChanged();
      return;
    }

    var rows = tbody.querySelectorAll('tr.js-grn-line');
    if (rows.length <= 1) {
      clearRowValues(row);
      row.classList.remove('d-none', 'grn-line-muted');
      destroyGrnProductSelect2In(row);
      initGrnProductSelect2();
      notifyChanged();
      return;
    }
    destroyGrnProductSelect2In(row);
    row.parentNode.removeChild(row);
    renumberGrnLines();
    initGrnProductSelect2();
    notifyChanged();
  }

  function focusQtyInRowFromProduct(productSelect) {
    var row = productSelect.closest('tr.js-grn-line');
    if (!row) return;
    var qty = row.querySelector('input[name$="-quantity"]');
    if (!qty) return;
    qty.focus();
    if (typeof qty.select === 'function') qty.select();
  }

  function bind() {
    var form = document.getElementById('grn-form');
    var tbody = document.getElementById('js-grn-lines-body');
    if (!form || !tbody) return;

    var addBtn = document.getElementById('js-grn-add-line');
    if (addBtn) {
      addBtn.addEventListener('click', function (e) {
        e.preventDefault();
        addLine();
      });
    }

    tbody.addEventListener('click', function (e) {
      var btn = e.target.closest && e.target.closest('.js-grn-remove-line');
      if (btn) {
        e.preventDefault();
        removeLine(btn);
      }
    });
    tbody.addEventListener('change', function (e) {
      var t = e.target;
      if (t && t.tagName === 'SELECT' && t.classList.contains('js-grn-product-select') && t.value) {
        focusQtyInRowFromProduct(t);
      }
    });
    if (window.jQuery && window.jQuery.fn.select2) {
      window.jQuery(tbody).on('select2:select', 'select.js-grn-product-select', function () {
        focusQtyInRowFromProduct(this);
      });
    }

    form.addEventListener('submit', function () {
      destroyGrnProductSelect2In(document);
      renumberGrnLines();
    });

    renumberGrnLines();
    initGrnProductSelect2();
    notifyChanged();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();

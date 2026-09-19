(function () {
  'use strict';

  function getCsrfToken() {
    var name = 'csrftoken=';
    var parts = document.cookie.split(';');
    for (var i = 0; i < parts.length; i++) {
      var c = parts[i].replace(/^\s+/, '');
      if (c.indexOf(name) === 0) return decodeURIComponent(c.substring(name.length));
    }
    var input = document.querySelector('[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }

  function swalIconForTags(tags) {
    var t = (tags || '').toLowerCase();
    if (t.indexOf('error') !== -1 || t.indexOf('danger') !== -1) return 'error';
    if (t.indexOf('warning') !== -1) return 'warning';
    if (t.indexOf('success') !== -1) return 'success';
    return 'info';
  }

  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
  }

  function swalTitleForIcon(icon) {
    if (icon === 'success') return 'Success';
    if (icon === 'error') return 'Error';
    if (icon === 'warning') return 'Warning';
    if (icon === 'info') return 'Information';
    return 'Notice';
  }

  /** Server-rendered Bootstrap alerts marked with .swal-flash → SweetAlert (then removed from DOM). */
  function flushBootstrapAlertsToSwal() {
    if (typeof Swal === 'undefined') return;
    var els = document.querySelectorAll('.alert.swal-flash');
    if (!els.length) return;
    var chain = Promise.resolve();
    els.forEach(function (el) {
      var html = (el.innerHTML || '').trim();
      var icon = 'info';
      if (el.classList.contains('alert-danger')) icon = 'error';
      else if (el.classList.contains('alert-warning')) icon = 'warning';
      else if (el.classList.contains('alert-success')) icon = 'success';
      else if (el.classList.contains('alert-info')) icon = 'info';
      el.remove();
      if (!html) return;
      chain = chain.then(function () {
        return Swal.fire({
          icon: icon,
          title: swalTitleForIcon(icon),
          html: html,
          confirmButtonText: 'OK',
          allowOutsideClick: false,
          allowEscapeKey: true,
          buttonsStyling: false,
          customClass: {
            popup: 'app-swal-popup',
            title: 'app-swal-title',
            htmlContainer: 'app-swal-html text-start',
            confirmButton: 'btn btn-primary',
          },
        });
      });
    });
  }

  function flushDjangoMessages() {
    if (typeof Swal === 'undefined') return;
    var wrap = document.getElementById('django-messages-data');
    if (!wrap) return;
    var items = wrap.querySelectorAll('.django-message-item');
    var chain = Promise.resolve();
    items.forEach(function (el) {
      var text = el.textContent.trim();
      if (!text) return;
      var icon = swalIconForTags(el.getAttribute('data-tags') || '');
      chain = chain.then(function () {
        return Swal.fire({
          icon: icon,
          title: swalTitleForIcon(icon),
          html: '<div class="django-message-text">' + escapeHtml(text) + '</div>',
          confirmButtonText: 'OK',
          allowOutsideClick: false,
          allowEscapeKey: true,
          buttonsStyling: false,
          customClass: {
            popup: 'app-swal-popup django-message-popup',
            title: 'app-swal-title django-message-title',
            htmlContainer: 'app-swal-html django-message-html text-start',
            confirmButton: 'btn btn-primary django-message-confirm',
          },
        });
      });
    });
  }

  /**
   * Simple notification (replaces window.alert).
   * @param {string} message
   * @param {{ icon?: string, title?: string }} [opts]
   */
  window.appAlert = function appAlert(message, opts) {
    opts = opts || {};
    var icon = opts.icon || 'info';
    var title = opts.title != null ? opts.title : swalTitleForIcon(icon);
    if (typeof Swal !== 'undefined') {
      return Swal.fire({
        icon: icon,
        title: title,
        text: message != null ? String(message) : '',
        confirmButtonText: 'OK',
        buttonsStyling: false,
        customClass: {
          popup: 'app-swal-popup',
          confirmButton: 'btn btn-primary',
        },
      });
    }
    window.alert(message != null ? String(message) : '');
    return Promise.resolve();
  };

  /**
   * Confirmation dialog (replaces window.confirm). Returns Promise<boolean>.
   * @param {string} message
   * @param {{ title?: string, confirmText?: string, cancelText?: string, icon?: string, confirmButtonClass?: string }} [opts]
   */
  window.appConfirm = function appConfirm(message, opts) {
    opts = opts || {};
    var title = opts.title || 'Please confirm';
    var confirmBtnClass = opts.confirmButtonClass || 'btn btn-primary';
    if (typeof Swal !== 'undefined') {
      return Swal.fire({
        icon: opts.icon || 'question',
        title: title,
        text: message != null ? String(message) : '',
        showCancelButton: true,
        confirmButtonText: opts.confirmText || 'OK',
        cancelButtonText: opts.cancelText || 'Cancel',
        reverseButtons: true,
        focusCancel: !!opts.focusCancel,
        buttonsStyling: false,
        customClass: {
          popup: 'app-swal-popup',
          title: 'app-swal-title',
          confirmButton: confirmBtnClass,
          cancelButton: 'btn btn-outline-secondary',
        },
      }).then(function (res) {
        return !!res.isConfirmed;
      });
    }
    return Promise.resolve(window.confirm(message != null ? String(message) : ''));
  };

  function fallbackListUrlAfterDelete(url) {
    var href = String(url || '');
    // /masters/farmers/47/delete/ → /masters/farmers/
    var farmerDelete = href.match(/^(.*\/farmers\/)\d+\/(?:quick-)?delete\/?$/i);
    if (farmerDelete) return farmerDelete[1];
    // Nested rate history: .../12/rate-history/5/delete/ → .../12/
    var rateHistory = href.replace(/\/rate-history\/\d+\/(?:quick-)?delete\/?$/i, '/');
    if (rateHistory !== href) return rateHistory;
    // Generic: .../<id>/delete/ → parent list
    var generic = href.replace(/\/\d+\/(?:quick-)?delete\/?$/i, '/');
    return generic !== href ? generic : '';
  }

  function goAfterDelete(url, preferredRedirect) {
    try {
      sessionStorage.removeItem('canmee.pageLoading');
    } catch (err) {}
    if (typeof window.hidePageLoad === 'function') {
      window.hidePageLoad();
    }
    if (preferredRedirect) {
      window.location.href = preferredRedirect;
      return;
    }
    var fallback = fallbackListUrlAfterDelete(url);
    if (fallback) {
      window.location.href = fallback;
      return;
    }
    window.location.reload();
  }

  function postDeleteUrl(url) {
    return fetch(url, {
      method: 'POST',
      headers: {
        'X-CSRFToken': getCsrfToken(),
        'X-Requested-With': 'XMLHttpRequest',
        Accept: 'application/json,text/html;q=0.9,*/*;q=0.8',
      },
      credentials: 'same-origin',
      redirect: 'manual',
    }).then(function (r) {
      if (r.type === 'opaqueredirect') {
        goAfterDelete(url);
        return { ok: true };
      }
      if (r.status === 302 || r.status === 303) {
        goAfterDelete(url, r.headers.get('Location') || '');
        return { ok: true };
      }
      if (r.status === 0) {
        goAfterDelete(url);
        return { ok: true };
      }
      if (r.ok) {
        var contentType = (r.headers.get('Content-Type') || '').toLowerCase();
        if (contentType.indexOf('application/json') !== -1) {
          return r.json().then(function (data) {
            goAfterDelete(url, data && data.redirect ? data.redirect : '');
            return { ok: true };
          });
        }
        goAfterDelete(url);
        return { ok: true };
      }
      var fallback =
        'The server rejected this delete. Related records may still exist.';
      var errType = (r.headers.get('Content-Type') || '').toLowerCase();
      if (errType.indexOf('application/json') !== -1) {
        return r.json().then(
          function (data) {
            return {
              ok: false,
              error: (data && (data.error || data.detail)) || fallback,
              error_html: data && data.error_html ? data.error_html : '',
              blockers: (data && data.blockers) || [],
              farmer_id: data && data.farmer_id ? data.farmer_id : '',
              can_delete: !!(data && data.can_delete),
            };
          },
          function () {
            return {
              ok: false,
              error: fallback,
              error_html: '',
              blockers: [],
              farmer_id: '',
              can_delete: false,
            };
          }
        );
      }
      return {
        ok: false,
        error: fallback,
        error_html: '',
        blockers: [],
        farmer_id: '',
        can_delete: false,
      };
    });
  }

  function initSwalDeletePosts() {
    if (typeof Swal === 'undefined') return;
    $(document).on('click', 'a.js-swal-delete-post', function (e) {
      e.preventDefault();
      var a = this;
      var url = a.getAttribute('href');
      if (!url) return;
      var title = a.getAttribute('data-swal-title') || 'Delete this record?';
      var text = a.getAttribute('data-swal-text') || '';
      Swal.fire({
        icon: 'warning',
        title: title,
        text: text || undefined,
        showCancelButton: true,
        confirmButtonText: 'Delete',
        cancelButtonText: 'Cancel',
        reverseButtons: true,
        buttonsStyling: false,
        customClass: {
          popup: 'app-swal-popup',
          confirmButton: 'btn btn-danger',
          cancelButton: 'btn btn-outline-secondary',
        },
      }).then(function (res) {
        if (!res.isConfirmed) return;
        if (a.getAttribute('data-reset-list-filters') === '1') {
          try { sessionStorage.setItem('smartDtResetFilters', '1'); } catch (err) {}
        }
        Swal.fire({
          title: 'Deleting…',
          allowOutsideClick: false,
          showConfirmButton: false,
          customClass: { popup: 'app-swal-popup' },
          didOpen: function () {
            Swal.showLoading();
          },
        });
        postDeleteUrl(url).then(function (result) {
          if (result && result.ok) return;
          showFarmerDeleteBlockedDialog(result || {}, url);
        });
      });
    });
  }

  function showFarmerDeleteBlockedDialog(result, farmerDeleteUrl) {
    var errorText =
      (result && result.error) ||
      'The server rejected this delete. Related records may still exist.';
    var farmerId = result && result.farmer_id ? String(result.farmer_id) : '';
    var payload = {
      icon: 'error',
      title: 'Could not delete',
      buttonsStyling: false,
      width: '36rem',
      customClass: {
        popup: 'app-swal-popup app-swal-popup--blockers',
        confirmButton: 'btn btn-primary',
        htmlContainer: 'app-swal-html',
      },
      didOpen: function () {
        bindFarmerBlockerDeleteButtons(farmerId, farmerDeleteUrl);
      },
    };
    if (result && result.error_html) {
      payload.html = result.error_html;
    } else {
      payload.text = errorText;
    }
    Swal.fire(payload);
  }

  function bindFarmerBlockerDeleteButtons(farmerId, farmerDeleteUrl) {
    if (!farmerId) return;
    var popup = Swal.getPopup();
    if (!popup) return;
    popup.querySelectorAll('.farmer-blocker-delete').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        var kind = btn.getAttribute('data-kind') || '';
        var id = btn.getAttribute('data-id') || '';
        if (!kind || !id) return;
        btn.disabled = true;
        var body = new URLSearchParams();
        body.set('kind', kind);
        body.set('id', id);
        var blockerUrl = (farmerDeleteUrl || '').replace(/\/(?:quick-)?delete\/?$/, '/delete-blocker/');
        if (!blockerUrl || blockerUrl === farmerDeleteUrl) {
          blockerUrl = '/masters/farmers/' + encodeURIComponent(farmerId) + '/delete-blocker/';
        }
        fetch(blockerUrl, {
          method: 'POST',
          headers: {
            'X-CSRFToken': getCsrfToken(),
            'X-Requested-With': 'XMLHttpRequest',
            Accept: 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
          },
          credentials: 'same-origin',
          body: body.toString(),
        })
          .then(function (r) {
            return r.json().then(function (data) {
              return { ok: r.ok, data: data || {} };
            });
          })
          .then(function (res) {
            if (!res.ok || !res.data.ok) {
              btn.disabled = false;
              Swal.fire({
                icon: 'error',
                title: 'Could not delete related record',
                text: (res.data && res.data.error) || 'Delete failed.',
                buttonsStyling: false,
                customClass: {
                  popup: 'app-swal-popup',
                  confirmButton: 'btn btn-primary',
                },
              });
              return;
            }
            if (res.data.cleared) {
              Swal.fire({
                icon: 'success',
                title: 'Related records cleared',
                text: 'You can delete the farmer now.',
                showCancelButton: true,
                confirmButtonText: 'Delete farmer',
                cancelButtonText: 'Close',
                reverseButtons: true,
                buttonsStyling: false,
                customClass: {
                  popup: 'app-swal-popup',
                  confirmButton: 'btn btn-danger',
                  cancelButton: 'btn btn-outline-secondary',
                },
              }).then(function (confirmRes) {
                if (!confirmRes.isConfirmed || !farmerDeleteUrl) return;
                Swal.fire({
                  title: 'Deleting…',
                  allowOutsideClick: false,
                  showConfirmButton: false,
                  customClass: { popup: 'app-swal-popup' },
                  didOpen: function () {
                    Swal.showLoading();
                  },
                });
                postDeleteUrl(farmerDeleteUrl).then(function (deleteResult) {
                  if (deleteResult && deleteResult.ok) return;
                  showFarmerDeleteBlockedDialog(deleteResult || {}, farmerDeleteUrl);
                });
              });
              return;
            }
            showFarmerDeleteBlockedDialog(res.data, farmerDeleteUrl);
          })
          .catch(function () {
            btn.disabled = false;
            Swal.fire({
              icon: 'error',
              title: 'Could not delete related record',
              text: 'Network error. Please try again.',
              buttonsStyling: false,
              customClass: {
                popup: 'app-swal-popup',
                confirmButton: 'btn btn-primary',
              },
            });
          });
      });
    });
  }

  function initSwalSubmitTriggers() {
    if (typeof Swal === 'undefined') return;
    $(document).on('click', 'form.js-swal-submit-guard button.js-swal-submit-trigger', function (e) {
      var btn = this;
      var form = btn.closest('form');
      if (!form || !form.classList.contains('js-swal-submit-guard')) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      var title = btn.getAttribute('data-swal-title') || 'Are you sure?';
      var text = btn.getAttribute('data-swal-text') || '';
      var oneInput = btn.getAttribute('data-one-input');
      var oneVal = btn.getAttribute('data-one-value');
      Swal.fire({
        icon: 'warning',
        title: title,
        text: text || undefined,
        showCancelButton: true,
        confirmButtonText: 'Yes, proceed',
        cancelButtonText: 'Cancel',
        reverseButtons: true,
        buttonsStyling: false,
        customClass: {
          popup: 'app-swal-popup',
          confirmButton: 'btn btn-danger',
          cancelButton: 'btn btn-outline-secondary',
        },
      }).then(function (res) {
        if (!res.isConfirmed) return;
        if (oneInput) {
          var el = document.getElementById(oneInput);
          if (el) el.value = oneVal != null ? String(oneVal) : '';
        }
        if (typeof form.requestSubmit === 'function') {
          form.requestSubmit(btn);
        } else {
          form.submit();
        }
      });
    });
  }

  function todayIsoDate() {
    var d = new Date();
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + m + '-' + day;
  }

  function ensureSettlementHiddenFields(form) {
    ['settlement_date', 'settlement_note'].forEach(function (name) {
      if (!form.querySelector('input[name="' + name + '"]')) {
        var input = document.createElement('input');
        input.type = 'hidden';
        input.name = name;
        form.appendChild(input);
      }
    });
  }

  function settlementModalHtml(text, defaultDate) {
    var today = defaultDate || todayIsoDate();
    return (
      '<p class="text-start small text-muted mb-3">' + escapeHtml(text || '') + '</p>' +
      '<label class="form-label text-start w-100 mb-1" for="swal-settlement-date">Date</label>' +
      '<div class="input-group mb-3 swal-settlement-date-group">' +
      '<input type="text" id="swal-settlement-date" class="form-control js-swal-settlement-datepicker" value="' + today + '" placeholder="Select date" autocomplete="off">' +
      '<span class="input-group-text js-swal-settlement-date-trigger" role="button" tabindex="0" aria-label="Open calendar"><i class="bi bi-calendar3"></i></span>' +
      '</div>' +
      '<label class="form-label text-start w-100 mb-1" for="swal-settlement-note">Note</label>' +
      '<input type="text" id="swal-settlement-note" class="form-control" maxlength="255" placeholder="Optional">'
    );
  }

  function destroySwalSettlementDatepicker() {
    var dateEl = document.getElementById('swal-settlement-date');
    if (dateEl && window.CanmeeDatepicker) {
      window.CanmeeDatepicker.destroy(dateEl);
    }
  }

  function initSwalSettlementDatepicker(defaultDate) {
    var dateEl = document.getElementById('swal-settlement-date');
    if (!dateEl || typeof window.CanmeeDatepicker === 'undefined') return null;
    var today = defaultDate || todayIsoDate();
    var instance = window.CanmeeDatepicker.initSingle(dateEl, {
      defaultDate: today,
      onReady: function (selectedDates, dateStr, fpInstance) {
        var trigger = document.querySelector('.js-swal-settlement-date-trigger');
        if (trigger) {
          trigger.addEventListener('click', function () {
            fpInstance.open();
          });
        }
        dateEl.addEventListener('click', function () {
          fpInstance.open();
        });
      },
    });
    return instance;
  }

  function focusSwalSettlementNote() {
    window.setTimeout(function () {
      var noteEl = document.getElementById('swal-settlement-note');
      if (noteEl) {
        noteEl.focus();
      }
    }, 0);
  }

  function readSwalSettlementDate() {
    var dateEl = document.getElementById('swal-settlement-date');
    if (!dateEl) return '';
    if (dateEl._flatpickr && dateEl._flatpickr.selectedDates.length) {
      return dateEl._flatpickr.formatDate(dateEl._flatpickr.selectedDates[0], 'Y-m-d');
    }
    return (dateEl.value || '').trim();
  }

  function initSwalSettlementTriggers() {
    if (typeof Swal === 'undefined') return;
    $(document).on('click', 'button.js-swal-settlement-trigger', function (e) {
      var btn = this;
      var form = btn.form || btn.closest('form');
      if (!form) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      if (btn.getAttribute('data-validate-goods-lines') === '1') {
        var lineChecks = form.querySelectorAll('.js-goods-pay-line:checked');
        if (!lineChecks.length) {
          Swal.fire({
            icon: 'warning',
            title: 'Select lines',
            text: 'Choose at least one line for partial payment.',
            buttonsStyling: false,
            customClass: {
              popup: 'app-swal-popup',
              confirmButton: 'btn btn-primary',
            },
          });
          return;
        }
      }
      var title = btn.getAttribute('data-swal-title') || 'Record settlement';
      var text = btn.getAttribute('data-swal-text') || '';
      var defaultDate = btn.getAttribute('data-swal-date') || todayIsoDate();
      Swal.fire({
        icon: 'question',
        title: title,
        html: settlementModalHtml(text, defaultDate),
        showCancelButton: true,
        confirmButtonText: 'Save',
        cancelButtonText: 'Cancel',
        reverseButtons: true,
        buttonsStyling: false,
        focusConfirm: false,
        customClass: {
          popup: 'app-swal-popup',
          confirmButton: 'btn btn-success',
          cancelButton: 'btn btn-outline-secondary',
        },
        didOpen: function () {
          initSwalSettlementDatepicker(defaultDate);
          focusSwalSettlementNote();
        },
        willClose: function () {
          destroySwalSettlementDatepicker();
        },
        preConfirm: function () {
          var dateVal = readSwalSettlementDate();
          var noteEl = document.getElementById('swal-settlement-note');
          if (!dateVal) {
            Swal.showValidationMessage('Date is required.');
            return false;
          }
          return {
            date: dateVal,
            note: noteEl ? noteEl.value.trim() : '',
          };
        },
      }).then(function (res) {
        if (!res.isConfirmed || !res.value) return;
        ensureSettlementHiddenFields(form);
        form.querySelector('input[name="settlement_date"]').value = res.value.date;
        form.querySelector('input[name="settlement_note"]').value = res.value.note;
        if (typeof form.requestSubmit === 'function') {
          form.requestSubmit(btn);
        } else {
          form.submit();
        }
      });
    });
  }

  function initSidebarGroups() {
    var STORAGE_KEY = 'canmee-sidebar-groups';
    var groups = document.querySelectorAll('.sidebar-group, .sidebar-subgroup');
    if (!groups.length) return;

    var saved = {};
    try {
      saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    } catch (e) {
      saved = {};
    }

    function setOpen(group, open, persist) {
      var toggle = group.querySelector(':scope > .sidebar-group-toggle, :scope > .sidebar-subgroup-toggle');
      group.classList.toggle('is-open', open);
      if (toggle) {
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      }
      if (persist) {
        var key = group.getAttribute('data-sidebar-group');
        if (key) {
          saved[key] = open;
          try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
          } catch (err) {
            /* ignore quota errors */
          }
        }
      }
    }

    groups.forEach(function (group) {
      var toggle = group.querySelector(':scope > .sidebar-group-toggle, :scope > .sidebar-subgroup-toggle');
      if (!toggle) return;

      toggle.addEventListener('click', function () {
        setOpen(group, !group.classList.contains('is-open'), true);
      });
    });

    var nav = document.querySelector('.sidebar-nav');
    var active = nav ? nav.querySelector('.nav-link.active') : null;

    groups.forEach(function (group) {
      group.classList.remove('has-active-child');
      setOpen(group, false, false);
    });

    if (active && nav) {
      var node = active.parentElement;
      while (node && node !== nav) {
        if (node.classList.contains('sidebar-group') || node.classList.contains('sidebar-subgroup')) {
          node.classList.add('has-active-child');
          setOpen(node, true, false);
        }
        node = node.parentElement;
      }
    }

    scrollActiveCategoryIntoView();
  }

  function scrollActiveCategoryIntoView() {
    var nav = document.querySelector('.sidebar-nav');
    if (!nav) return;

    var active = nav.querySelector('.nav-link.active');
    if (!active) return;

    function findCategoryAnchor(link) {
      var topGroup = link.closest('.sidebar-group');
      if (topGroup) {
        var groupToggle = topGroup.querySelector(':scope > .sidebar-group-toggle');
        if (groupToggle) return groupToggle;
      }
      return link;
    }

    function doScroll() {
      var anchor = findCategoryAnchor(active);
      if (!anchor) return;

      var navRect = nav.getBoundingClientRect();
      var anchorRect = anchor.getBoundingClientRect();
      var target = nav.scrollTop + (anchorRect.top - navRect.top) - 8;

      nav.classList.add('is-scrolling-active');
      nav.scrollTop = Math.max(0, target);
      window.requestAnimationFrame(function () {
        nav.classList.remove('is-scrolling-active');
      });
    }

    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(doScroll);
    });
    window.setTimeout(doScroll, 100);
  }

  function initSidebar() {
    var appShell = document.querySelector('.app-shell');
    var sidebar = document.getElementById('sidebar');
    var toggle = document.getElementById('sidebarToggle');
    var backdrop = document.getElementById('sidebarBackdrop');
    if (!sidebar || !toggle) return;
    var desktopBreakpoint = 992;

    function isDesktop() {
      return window.innerWidth >= desktopBreakpoint;
    }

    function openNav() {
      if (isDesktop()) {
        if (appShell) appShell.classList.remove('sidebar-collapsed');
        return;
      }
      sidebar.classList.add('show');
      if (backdrop) {
        backdrop.removeAttribute('hidden');
        backdrop.classList.add('show');
      }
      document.body.style.overflow = 'hidden';
    }

    function closeNav() {
      if (isDesktop()) {
        if (appShell) appShell.classList.add('sidebar-collapsed');
        return;
      }
      sidebar.classList.remove('show');
      if (backdrop) {
        backdrop.classList.remove('show');
        backdrop.setAttribute('hidden', '');
      }
      document.body.style.overflow = '';
    }

    toggle.addEventListener('click', function () {
      if (isDesktop()) {
        if (appShell && appShell.classList.contains('sidebar-collapsed')) openNav();
        else closeNav();
        return;
      }
      if (sidebar.classList.contains('show')) {
        closeNav();
      } else {
        openNav();
      }
    });

    if (backdrop) {
      backdrop.addEventListener('click', closeNav);
    }

    window.addEventListener('resize', function () {
      if (isDesktop()) {
        sidebar.classList.remove('show');
        if (backdrop) {
          backdrop.classList.remove('show');
          backdrop.setAttribute('hidden', '');
        }
        document.body.style.overflow = '';
      }
    });
  }

  function getExportColumnIndexes(tableEl) {
    var idx = [];
    if (!tableEl || !tableEl.tHead || !tableEl.tHead.rows.length) return idx;
    var headers = tableEl.tHead.rows[0].cells;
    for (var i = 0; i < headers.length; i++) {
      var th = headers[i];
      var text = (th.textContent || '').trim().toLowerCase();
      if (text === 'actions' || text === 'action') continue;
      if (th.querySelector('input[type="checkbox"]')) continue;
      if (th.classList.contains('no-export')) continue;
      idx.push(i);
    }
    return idx;
  }

  function formatAccountingAmount(value) {
    if (value == null) return '';
    var original = String(value).replace(/\s+/g, ' ').trim();
    if (!original || original === '—' || original === '–' || original === '-') return original;
    var negative = false;
    var s = original;
    if (/^\(.*\)$/.test(s)) {
      negative = true;
      s = s.slice(1, -1).trim();
    }
    s = s.replace(/,/g, '');
    if (s.charAt(0) === '-' || s.charAt(0) === '\u2212') {
      negative = true;
      s = s.slice(1);
    }
    if (!/^\d+(\.\d+)?$/.test(s)) return original;
    var n = parseFloat(s);
    if (isNaN(n)) return original;
    var parts = Math.abs(n).toFixed(2).split('.');
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    var out = parts.join('.');
    return negative && n !== 0 ? '(' + out + ')' : out;
  }

  function isAccountingExportCell(node) {
    if (!node || !node.classList) return false;
    if (node.classList.contains('dt-num') || node.classList.contains('dt-foot-total')) return true;
    var table = node.closest ? node.closest('table') : null;
    if (table && table.getAttribute('data-export-accounting') === '1' && node.classList.contains('text-end')) {
      return true;
    }
    return false;
  }

  function exportBodyText(data, row, column, node) {
    var text;
    if (!node) {
      text = $('<div>').html(data).text().replace(/\s+/g, ' ').trim();
      if (/^-?[\d,]+(\.\d+)?$/.test(text) || /^\(.*\)$/.test(text)) {
        return formatAccountingAmount(text);
      }
      return text;
    }
    var clone = $(node).clone();
    clone.find('input, button, select, textarea').remove();
    text = clone.text().replace(/\s+/g, ' ').trim();
    if (!isAccountingExportCell(node)) return text;
    var orderVal = node.getAttribute && node.getAttribute('data-order');
    if (orderVal != null && orderVal !== '' && /^-?[\d.]+$/.test(String(orderVal).replace(/,/g, ''))) {
      return formatAccountingAmount(orderVal);
    }
    return formatAccountingAmount(text);
  }

  function getExportMoneyColumnFlags(tableEl, exportColumns) {
    var flags = {};
    if (!tableEl || !tableEl.tHead || !tableEl.tHead.rows.length) return flags;
    var headers = tableEl.tHead.rows[0].cells;
    var firstRow = tableEl.tBodies.length && tableEl.tBodies[0].rows.length ? tableEl.tBodies[0].rows[0].cells : null;
    var footRow = tableEl.tFoot && tableEl.tFoot.rows.length ? tableEl.tFoot.rows[0].cells : null;
    (exportColumns || []).forEach(function (srcIdx, destIdx) {
      var th = headers[srcIdx];
      var td = firstRow ? firstRow[srcIdx] : null;
      var tf = footRow ? footRow[srcIdx] : null;
      if (
        (td && isAccountingExportCell(td)) ||
        (tf && isAccountingExportCell(tf)) ||
        (th && th.classList && th.classList.contains('text-end') && !th.classList.contains('no-export'))
      ) {
        flags[destIdx] = true;
      }
    });
    return flags;
  }

  function applyAccountingPdfTable(doc, moneyFlags) {
    if (!doc || !moneyFlags) return;
    var tables = [];
    function collect(node) {
      if (!node) return;
      if (Array.isArray(node)) {
        node.forEach(collect);
        return;
      }
      if (node.table && node.table.body) tables.push(node.table);
      if (node.content) collect(node.content);
      if (node.stack) collect(node.stack);
      if (node.columns) collect(node.columns);
    }
    collect(doc.content);
    var table = tables.length ? tables[tables.length - 1] : null;
    if (!table) return;
    table.body.forEach(function (row, rIdx) {
      if (!row) return;
      row.forEach(function (cell, cIdx) {
        if (!moneyFlags[cIdx]) return;
        var obj = cell && typeof cell === 'object' ? cell : { text: cell };
        obj.alignment = 'right';
        if (rIdx > 0 && typeof obj.text === 'string') {
          obj.text = formatAccountingAmount(obj.text);
        }
        row[cIdx] = obj;
      });
    });
  }

  function exportMenuItem(icon, label, hint) {
    return (
      '<span class="dt-export-item">' +
        '<span class="dt-export-item__icon"><i class="bi ' + icon + '" aria-hidden="true"></i></span>' +
        '<span class="dt-export-item__text">' +
          '<span class="dt-export-item__label">' + label + '</span>' +
          (hint ? '<span class="dt-export-item__hint">' + hint + '</span>' : '') +
        '</span>' +
      '</span>'
    );
  }

  function printPageStatement(tableEl) {
    var title = (tableEl && tableEl.getAttribute('data-print-title')) || 'Collection point loan register';
    var originalTitle = document.title;
    document.title = title;
    window.print();
    var restore = function () {
      document.title = originalTitle;
      window.removeEventListener('afterprint', restore);
    };
    window.addEventListener('afterprint', restore);
  }

  function formatBankGeneratedAt() {
    var d = new Date();
    var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    function pad(n) {
      return n < 10 ? '0' + n : String(n);
    }
    return (
      pad(d.getDate()) +
      ' ' +
      months[d.getMonth()] +
      ' ' +
      d.getFullYear() +
      ', ' +
      pad(d.getHours()) +
      ':' +
      pad(d.getMinutes())
    );
  }

  function firstVisibleText(el) {
    if (!el) return '';
    var clone = el.cloneNode(true);
    Array.prototype.forEach.call(
      clone.querySelectorAll('.point-finance-pane__tools, .dt-search, .dt-buttons, .dt-smart-toolbar'),
      function (node) {
        node.remove();
      }
    );
    return (clone.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function getTablePrintTitle(tableEl, fallback) {
    if (!tableEl) return fallback || 'Report';
    var explicit = tableEl.getAttribute('data-print-title');
    if (explicit && explicit.trim()) return explicit.trim();
    var pane = tableEl.closest('.point-finance-pane');
    if (pane) {
      var paneTitle = firstVisibleText(pane.querySelector(':scope > .point-finance-pane__title'));
      if (paneTitle) return paneTitle;
    }
    var card = tableEl.closest('.card, .point-detail-card, .farmer-collection-card, .smart-list-card');
    if (card) {
      var label = firstVisibleText(
        card.querySelector(
          '.farmer-milk-actions-bar__label, .point-section-title, .farmer-section-title, .point-finance-head__title, .farmer-financial-block__title, .cp-loan-statement__title'
        )
      );
      if (label) return label;
    }
    return fallback || 'Report';
  }

  function getBankPrintContext(tableEl) {
    var host = tableEl && tableEl.closest ? tableEl.closest('[data-bank-print]') : null;
    if (!host) return null;
    return {
      company: host.getAttribute('data-bank-company') || 'CANMEE DAIRIES (PVT) LTD',
      phone: host.getAttribute('data-bank-phone') || '071 543 5019',
      logo: host.getAttribute('data-bank-logo') || '',
      pointNo: host.getAttribute('data-bank-point-no') || '—',
      pointName: host.getAttribute('data-bank-point-name') || '—',
      route: host.getAttribute('data-bank-route') || '—',
      branch: host.getAttribute('data-bank-branch') || '—',
      location: host.getAttribute('data-bank-location') || '—',
      period: host.getAttribute('data-bank-period') || '—',
      generated: formatBankGeneratedAt(),
    };
  }

  function bankPrintCss() {
    return (
      '@page{size:A4 portrait;margin:14mm 14mm 16mm;}' +
      'body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:11px;color:#111;margin:0;}' +
      '.bank-doc{margin:0 0 10pt;padding:0 0 8pt;border-bottom:2.25pt solid #111;}' +
      '.bank-brand{display:flex;align-items:center;gap:8pt;margin-bottom:8pt;}' +
      '.bank-logo{width:36pt;height:36pt;object-fit:contain;}' +
      '.bank-company{font-size:12pt;font-weight:800;letter-spacing:.04em;line-height:1.2;}' +
      '.bank-line{font-size:8pt;color:#333;}' +
      '.bank-issued{margin-left:auto;text-align:right;font-size:8pt;color:#333;}' +
      '.bank-issued strong{display:block;color:#111;font-size:9pt;}' +
      '.bank-title{margin:6pt 0 0;font-size:14pt;font-weight:800;letter-spacing:.02em;text-transform:uppercase;}' +
      '.bank-subtitle{margin:2pt 0 8pt;font-size:9pt;color:#333;}' +
      '.bank-id{width:100%;border-collapse:collapse;font-size:8.5pt;margin:0 0 10pt;}' +
      '.bank-id th,.bank-id td{border:.6pt solid #222;padding:4pt 6pt;text-align:left;vertical-align:top;}' +
      '.bank-id th{width:14%;background:#f3f3f3;font-size:7.5pt;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:#333;}' +
      'table.data{border-collapse:collapse;width:100%;}' +
      'table.data th,table.data td{border:1px solid #222;padding:4px 6px;vertical-align:top;}' +
      'table.data th{background:#f3f3f3;font-weight:700;font-size:7.5pt;letter-spacing:.04em;text-transform:uppercase;}' +
      'td.text-end,th.text-end{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;}' +
      'tfoot td,tfoot th{font-weight:700;}' +
      '.bank-footer{display:flex;justify-content:space-between;gap:12pt;margin-top:12pt;padding-top:6pt;border-top:.4pt solid #888;font-size:7.5pt;color:#444;font-style:italic;}'
    );
  }

  function bankPrintHeaderHtml(ctx, title) {
    var subtitle =
      escapeHtml(ctx.pointNo) +
      ' — ' +
      escapeHtml(ctx.pointName) +
      (ctx.route && ctx.route !== '—' ? ' · ' + escapeHtml(ctx.route) : '') +
      (ctx.branch && ctx.branch !== '—' ? ' · ' + escapeHtml(ctx.branch) : '');
    var logo = ctx.logo
      ? '<img src="' + escapeHtml(ctx.logo) + '" alt="" class="bank-logo" width="48" height="48">'
      : '';
    return (
      '<header class="bank-doc">' +
        '<div class="bank-brand">' +
          logo +
          '<div><div class="bank-company">' +
          escapeHtml(ctx.company) +
          '</div><div class="bank-line">Phone: ' +
          escapeHtml(ctx.phone) +
          '</div></div>' +
          '<div class="bank-issued"><span>Generated</span><strong>' +
          escapeHtml(ctx.generated) +
          '</strong></div>' +
        '</div>' +
        '<h1 class="bank-title">' +
        escapeHtml(title) +
        '</h1>' +
        '<p class="bank-subtitle">' +
        subtitle +
        '</p>' +
        '<table class="bank-id"><tbody>' +
        '<tr><th>Point no</th><td>' +
        escapeHtml(ctx.pointNo) +
        '</td><th>Collection point</th><td>' +
        escapeHtml(ctx.pointName) +
        '</td><th>Period</th><td>' +
        escapeHtml(ctx.period) +
        '</td></tr>' +
        '<tr><th>Route</th><td>' +
        escapeHtml(ctx.route) +
        '</td><th>Branch</th><td>' +
        escapeHtml(ctx.branch) +
        '</td><th>Location</th><td>' +
        escapeHtml(ctx.location) +
        '</td></tr>' +
        '</tbody></table>' +
      '</header>'
    );
  }

  var bankLogoCache = {};
  function withBankLogoDataUrl(url, done) {
    if (!url) {
      done(null);
      return;
    }
    if (Object.prototype.hasOwnProperty.call(bankLogoCache, url)) {
      done(bankLogoCache[url]);
      return;
    }
    var img = new Image();
    img.onload = function () {
      try {
        var canvas = document.createElement('canvas');
        canvas.width = img.naturalWidth || 96;
        canvas.height = img.naturalHeight || 96;
        canvas.getContext('2d').drawImage(img, 0, 0);
        bankLogoCache[url] = canvas.toDataURL('image/png');
      } catch (err) {
        bankLogoCache[url] = null;
      }
      done(bankLogoCache[url]);
    };
    img.onerror = function () {
      bankLogoCache[url] = null;
      done(null);
    };
    img.src = url;
  }

  function applyBankPdfLetterhead(doc, ctx, title, logoDataUrl) {
    doc.pageSize = 'A4';
    doc.pageMargins = [40, 36, 40, 48];
    doc.defaultStyle = doc.defaultStyle || {};
    doc.defaultStyle.fontSize = 8;
    doc.defaultStyle.color = '#111111';
    doc.styles = doc.styles || {};
    doc.styles.tableHeader = {
      bold: true,
      fontSize: 7.5,
      color: '#111111',
      fillColor: '#f3f3f3',
    };
    doc.styles.title = { fontSize: 13, bold: true, margin: [0, 0, 0, 4] };
    doc.styles.idTh = {
      bold: true,
      fontSize: 7,
      color: '#333333',
      fillColor: '#f3f3f3',
    };
    doc.info = {
      title: title,
      author: 'Canmee Dairies (Pvt) Ltd',
    };
    var brandColumns = [];
    if (logoDataUrl) {
      doc.images = doc.images || {};
      doc.images.bankLogo = logoDataUrl;
      brandColumns.push({ image: 'bankLogo', width: 36, margin: [0, 0, 8, 0] });
    }
    brandColumns.push({
      stack: [
        { text: ctx.company, bold: true, fontSize: 11 },
        { text: 'Phone: ' + ctx.phone, fontSize: 8, color: '#333333', margin: [0, 2, 0, 0] },
      ],
      width: '*',
    });
    brandColumns.push({
      stack: [
        { text: 'Generated', fontSize: 8, color: '#333333', alignment: 'right' },
        { text: ctx.generated, bold: true, fontSize: 9, alignment: 'right' },
      ],
      width: 110,
    });
    var letterhead = [
      { columns: brandColumns, margin: [0, 0, 0, 8] },
      {
        canvas: [{ type: 'line', x1: 0, y1: 0, x2: 515, y2: 0, lineWidth: 1.5, lineColor: '#111111' }],
        margin: [0, 0, 0, 8],
      },
      { text: String(title || '').toUpperCase(), style: 'title' },
      {
        text: ctx.pointNo + ' — ' + ctx.pointName,
        fontSize: 9,
        color: '#333333',
        margin: [0, 0, 0, 8],
      },
      {
        table: {
          widths: ['14%', '19%', '18%', '16%', '14%', '19%'],
          body: [
            [
              { text: 'POINT NO', style: 'idTh' },
              ctx.pointNo,
              { text: 'COLLECTION POINT', style: 'idTh' },
              ctx.pointName,
              { text: 'PERIOD', style: 'idTh' },
              ctx.period,
            ],
            [
              { text: 'ROUTE', style: 'idTh' },
              ctx.route,
              { text: 'BRANCH', style: 'idTh' },
              ctx.branch,
              { text: 'LOCATION', style: 'idTh' },
              ctx.location,
            ],
          ],
        },
        layout: {
          hLineWidth: function () {
            return 0.6;
          },
          vLineWidth: function () {
            return 0.6;
          },
          hLineColor: function () {
            return '#222222';
          },
          vLineColor: function () {
            return '#222222';
          },
          paddingLeft: function () {
            return 6;
          },
          paddingRight: function () {
            return 6;
          },
          paddingTop: function () {
            return 4;
          },
          paddingBottom: function () {
            return 4;
          },
        },
        margin: [0, 0, 0, 12],
      },
    ];
    var rest = (doc.content || []).slice();
    if (rest.length && rest[0] && rest[0].text) {
      rest.shift();
    }
    doc.content = letterhead.concat(rest);
    doc.footer = function () {
      return {
        columns: [
          {
            text: 'This is a computer-generated statement and does not require a signature.',
            italics: true,
            fontSize: 7,
            color: '#444444',
          },
          {
            text: 'Canmee Dairies (Pvt) Ltd · Confidential',
            alignment: 'right',
            fontSize: 7,
            color: '#444444',
          },
        ],
        margin: [40, 0, 40, 20],
      };
    };
  }

  function buildSmartExportButtons(tableEl, exportColumns, exportFormat, title) {
    var statementPrint = tableEl && tableEl.getAttribute('data-print-mode') === 'statement';
    var pdfUrl = tableEl && tableEl.getAttribute('data-pdf-url');
    var bankCtx = getBankPrintContext(tableEl);
    var printTitle = getTablePrintTitle(tableEl, title || document.title || 'Report');
    var btnClass = 'btn btn-sm btn-outline-secondary dt-btn-modern dt-export-trigger';
    var pdfButton;
    var moneyFlags = getExportMoneyColumnFlags(tableEl, exportColumns);
    var hasFooter = !!(tableEl && tableEl.tFoot);
    var pdfExportOptions = {
      columns: exportColumns,
      format: exportFormat,
      footer: hasFooter,
    };
    if (pdfUrl) {
      pdfButton = {
        text: exportMenuItem('bi-file-earmark-pdf', 'PDF', statementPrint ? 'Register' : 'Document'),
        className: 'dt-export-choice dt-export-choice--pdf',
        action: function () {
          window.location.assign(pdfUrl);
        },
      };
    } else {
      pdfButton = {
        extend: 'pdfHtml5',
        text: exportMenuItem('bi-file-earmark-pdf', 'PDF', bankCtx ? 'Statement' : 'Document'),
        className: 'dt-export-choice dt-export-choice--pdf',
        exportOptions: pdfExportOptions,
        title: printTitle,
        filename: String(printTitle || 'statement').replace(/[^\w]+/g, '-'),
        pageSize: 'A4',
        customize: function (doc) {
          applyAccountingPdfTable(doc, moneyFlags);
        },
      };
      if (bankCtx) {
        var bankLogoDataUrl = null;
        pdfButton.customize = function (doc) {
          applyBankPdfLetterhead(doc, bankCtx, printTitle, bankLogoDataUrl);
          applyAccountingPdfTable(doc, moneyFlags);
        };
        pdfButton.action = function (e, dt, node, config) {
          var self = this;
          withBankLogoDataUrl(bankCtx.logo, function (dataUrl) {
            bankLogoDataUrl = dataUrl;
            $.fn.dataTable.ext.buttons.pdfHtml5.action.call(self, e, dt, node, config);
          });
        };
      }
    }
    return [
      {
        extend: 'collection',
        text: '<i class="bi bi-box-arrow-up me-1"></i>Export',
        className: btnClass + ' dt-export-collection',
        autoClose: true,
        background: false,
        closeButton: false,
        dropup: false,
        align: 'button-right',
        popoverTitle: 'Export',
        buttons: [
          {
            extend: 'copyHtml5',
            text: exportMenuItem('bi-clipboard', 'Copy', 'Clipboard'),
            className: 'dt-export-choice',
            exportOptions: { columns: exportColumns, format: exportFormat },
          },
          {
            extend: 'excelHtml5',
            text: exportMenuItem('bi-file-earmark-excel', 'Excel', 'Workbook (.xlsx)'),
            className: 'dt-export-choice dt-export-choice--excel',
            exportOptions: { columns: exportColumns, format: exportFormat },
          },
          {
            extend: 'csvHtml5',
            text: exportMenuItem('bi-filetype-csv', 'CSV', 'Spreadsheet'),
            className: 'dt-export-choice dt-export-choice--csv',
            exportOptions: { columns: exportColumns, format: exportFormat },
          },
          {
            text: exportMenuItem('bi-printer', 'Print', statementPrint ? 'Register' : bankCtx ? 'Statement' : 'Table'),
            className: 'dt-export-choice dt-export-choice--print',
            action: function (e, dt) {
              if (statementPrint) {
                printPageStatement(tableEl);
                return;
              }
              runDatatablePrint(dt, exportColumns, printTitle, tableEl);
            },
          },
          pdfButton,
        ],
      },
    ];
  }

  function pinDtExportCollection(collection, trigger) {
    if (!collection) return;
    var host = trigger;
    if (!host || !host.getBoundingClientRect) {
      host = document.querySelector('.dt-export-collection[aria-expanded="true"]');
    }
    if (!host) return;
    if (collection.parentElement !== document.body) {
      document.body.appendChild(collection);
    }
    var rect = host.getBoundingClientRect();
    var menuH = collection.offsetHeight || 0;
    var menuW = Math.max(collection.offsetWidth || 0, 264);
    var gap = 6;
    var top = rect.bottom + gap;
    if (top + menuH > window.innerHeight - 8) {
      top = Math.max(8, rect.top - menuH - gap);
    }
    var left = rect.right - menuW;
    var maxLeft = Math.max(8, window.innerWidth - menuW - 8);
    if (left < 8) left = 8;
    if (left > maxLeft) left = maxLeft;
    collection.style.setProperty('position', 'fixed', 'important');
    collection.style.setProperty('top', Math.round(top) + 'px', 'important');
    collection.style.setProperty('left', Math.round(left) + 'px', 'important');
    collection.style.setProperty('right', 'auto', 'important');
    collection.style.setProperty('bottom', 'auto', 'important');
    collection.style.setProperty('margin', '0', 'important');
    collection.style.setProperty('transform', 'none', 'important');
    collection.style.setProperty('z-index', '4000', 'important');
  }

  function initDtExportMenuPin() {
    if (document.documentElement.dataset.dtExportPin === '1') return;
    document.documentElement.dataset.dtExportPin = '1';
    document.addEventListener(
      'click',
      function (e) {
        var trigger = e.target && e.target.closest ? e.target.closest('.dt-export-collection') : null;
        if (!trigger) return;
        window.setTimeout(function () {
          pinDtExportCollection(document.querySelector('div.dt-button-collection'), trigger);
        }, 0);
        window.setTimeout(function () {
          pinDtExportCollection(document.querySelector('div.dt-button-collection'), trigger);
        }, 60);
        window.setTimeout(function () {
          pinDtExportCollection(document.querySelector('div.dt-button-collection'), trigger);
        }, 160);
      },
      true
    );
  }

  function buildLegacyExportButtons(exportColumns, exportFormat) {
    var btnClass = 'btn btn-sm btn-outline-secondary dt-btn-modern dt-btn-legacy';
    return [
      {
        extend: 'copyHtml5',
        text: '<i class="bi bi-clipboard me-1"></i>Copy',
        className: btnClass,
        exportOptions: { columns: exportColumns, format: exportFormat },
      },
      {
        extend: 'excelHtml5',
        text: '<i class="bi bi-file-earmark-excel me-1"></i>Excel',
        className: btnClass,
        exportOptions: { columns: exportColumns, format: exportFormat },
      },
      {
        extend: 'csvHtml5',
        text: '<i class="bi bi-filetype-csv me-1"></i>CSV',
        className: btnClass,
        exportOptions: { columns: exportColumns, format: exportFormat },
      },
      {
        text: '<i class="bi bi-printer me-1"></i>Print',
        className: btnClass,
        action: function (e, dt) {
          runDatatablePrint(dt, exportColumns, document.title || 'Report');
        },
      },
      {
        extend: 'pdfHtml5',
        text: '<i class="bi bi-file-earmark-pdf me-1"></i>PDF',
        className: btnClass,
        exportOptions: { columns: exportColumns, format: exportFormat },
      },
    ];
  }

  function smartFooterTotals(api, numericCols) {
    var totals = {};
    numericCols.forEach(function (col) {
      totals[col] = 0;
    });
    api.rows({ search: 'applied' }).every(function () {
      var rowIdx = this.index();
      numericCols.forEach(function (col) {
        var node = api.cell(rowIdx, col).node();
        var orderVal = node ? node.getAttribute('data-order') : null;
        var raw = orderVal != null && orderVal !== '' ? orderVal : $(node).text();
        var num = parseFloat(String(raw).replace(/,/g, ''));
        if (!isNaN(num)) totals[col] += num;
      });
    });
    numericCols.forEach(function (col) {
      var cell = api.column(col).footer();
      if (cell) {
        $(cell).html(formatAccountingAmount(totals[col]));
      }
    });
  }

  function bindSmartTableFilters(api, tableEl) {
    var filtersId = tableEl.getAttribute('data-filters-target');
    if (!filtersId) return;
    var filtersEl = document.getElementById(filtersId);
    if (!filtersEl) return;
    var activeFilter = 'all';
    var branchFilterId = tableEl.getAttribute('data-branch-filter-target');
    var routeFilterId = tableEl.getAttribute('data-route-filter-target');
    var pointFilterId = tableEl.getAttribute('data-point-filter-target');
    var branchFilterEl = branchFilterId ? document.getElementById(branchFilterId) : null;
    var routeFilterEl = routeFilterId ? document.getElementById(routeFilterId) : null;
    var pointFilterEl = pointFilterId ? document.getElementById(pointFilterId) : null;
    var activeBranch = 'all';
    var activeRoute = 'all';
    var activePoint = 'all';
    var tableNode = tableEl;
    $.fn.dataTable.ext.search.push(function (settings, data, dataIndex) {
      if (settings.nTable !== tableNode) return true;
      var row = api.row(dataIndex).node();
      if (!row) return true;
      if (activeBranch !== 'all') {
        var rowBranchIds = row.getAttribute('data-branch-ids') || '';
        if (rowBranchIds) {
          var branchIdList = rowBranchIds.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
          if (branchIdList.indexOf(activeBranch) === -1) return false;
        } else {
          var rowBranchId = row.getAttribute('data-branch-id') || '';
          if (rowBranchId !== activeBranch) return false;
        }
      }
      if (activeRoute !== 'all') {
        var rowRouteId = row.getAttribute('data-route-id') || '';
        if (rowRouteId !== activeRoute) return false;
      }
      if (activePoint !== 'all') {
        var rowPointId = row.getAttribute('data-point-id') || '';
        if (rowPointId !== activePoint) return false;
      }
      if (activeFilter === 'all') return true;
      return row.classList.contains('smart-row--' + activeFilter);
    });
    filtersEl.addEventListener('click', function (e) {
      var chip = e.target.closest('[data-smart-filter]');
      if (!chip) return;
      activeFilter = chip.getAttribute('data-smart-filter') || 'all';
      filtersEl.querySelectorAll('[data-smart-filter]').forEach(function (btn) {
        btn.classList.toggle('is-active', btn === chip);
      });
      api.draw();
    });
    if (branchFilterEl) {
      branchFilterEl.addEventListener('change', function () {
        activeBranch = branchFilterEl.value || 'all';
        api.draw();
      });
    }
    if (routeFilterEl) {
      routeFilterEl.addEventListener('change', function () {
        activeRoute = routeFilterEl.value || 'all';
        api.draw();
      });
    }
    if (pointFilterEl) {
      pointFilterEl.addEventListener('change', function () {
        activePoint = pointFilterEl.value || 'all';
        api.draw();
      });
    }
    return {
      getActiveBranch: function () { return activeBranch; },
      setActiveBranch: function (value) { activeBranch = value || 'all'; },
      getActiveRoute: function () { return activeRoute; },
      setActiveRoute: function (value) { activeRoute = value || 'all'; },
      getActivePoint: function () { return activePoint; },
      setActivePoint: function (value) { activePoint = value || 'all'; },
      redraw: function () { api.draw(); },
    };
  }

  function sequenceColumnIndex(tableEl) {
    if (!tableEl || !tableEl.tHead || !tableEl.tHead.rows.length) return -1;
    var cells = tableEl.tHead.rows[0].cells;
    for (var i = 0; i < cells.length; i++) {
      var cls = ' ' + (cells[i].className || '') + ' ';
      if (cls.indexOf(' dt-seq ') !== -1) return i;
    }
    if (cells[0] && (cells[0].textContent || '').trim() === '#') return 0;
    return -1;
  }

  function tableHasSequenceColumn(tableEl) {
    return sequenceColumnIndex(tableEl) !== -1;
  }

  function sequenceColumnDef(tableEl) {
    var idx = sequenceColumnIndex(tableEl);
    return {
      targets: idx < 0 ? 0 : idx,
      orderable: false,
      searchable: false,
    };
  }

  function refreshSequenceNumbers(api) {
    var tableEl = api.table().node();
    var idx = sequenceColumnIndex(tableEl);
    if (idx < 0) idx = 0;
    var info = api.page.info();
    var cells = api.column(idx, { page: 'current', order: 'applied', search: 'applied' }).nodes();
    for (var i = 0; i < cells.length; i++) {
      var cell = cells[i];
      if (!cell) continue;
      var row = cell.parentElement;
      if (!row || row.parentElement.tagName === 'TFOOT' || row.classList.contains('child')) continue;
      cell.textContent = String(info.start + i + 1);
    }
  }

  var datatablePrintCss =
    '@page{margin:12mm;}' +
    'body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:11px;color:#111;margin:0;}' +
    'h1{font-size:16px;margin:0 0 10px;font-weight:700;}' +
    'table{border-collapse:collapse;width:100%;}' +
    'th,td{border:1px solid #cbd5e1;padding:4px 6px;vertical-align:top;}' +
    'th{background:#f1f5f9;font-weight:600;}' +
    'td.text-end,th.text-end{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;}' +
    'tfoot td,tfoot th{font-weight:700;}';

  function runDatatablePrint(dt, exportColumns, title, tableEl) {
    var hasFooter = !!(tableEl && tableEl.tFoot);
    var moneyFlags = getExportMoneyColumnFlags(tableEl, exportColumns);
    var exportData = dt.buttons.exportData({
      columns: exportColumns,
      footer: hasFooter,
      format: {
        header: function (data, column, node) {
          if (node) {
            return $(node).text().replace(/\s+/g, ' ').trim();
          }
          return String(data || '').replace(/\s+/g, ' ').trim();
        },
        body: exportBodyText,
        footer: exportBodyText,
      },
    });
    var bankCtx = getBankPrintContext(tableEl);
    var safeTitle = escapeHtml(title || document.title || 'Report');
    var parts = [
      '<!DOCTYPE html><html><head><meta charset="utf-8"><title>',
      safeTitle,
      '</title><style>',
      bankCtx ? bankPrintCss() : datatablePrintCss,
      '</style></head><body>',
    ];
    function moneyClass(i) {
      return moneyFlags[i] ? ' class="text-end"' : '';
    }
    if (bankCtx) {
      parts.push(bankPrintHeaderHtml(bankCtx, title || 'Report'));
      parts.push('<table class="data"><thead><tr>');
    } else {
      parts.push('<h1>', safeTitle, '</h1><table><thead><tr>');
    }
    (exportData.header || []).forEach(function (header, i) {
      parts.push('<th' + moneyClass(i) + '>', escapeHtml(header), '</th>');
    });
    parts.push('</tr></thead><tbody>');
    (exportData.body || []).forEach(function (row) {
      parts.push('<tr>');
      row.forEach(function (cell, i) {
        parts.push('<td' + moneyClass(i) + '>', escapeHtml(cell), '</td>');
      });
      parts.push('</tr>');
    });
    parts.push('</tbody>');
    if (exportData.footer && exportData.footer.length) {
      parts.push('<tfoot><tr>');
      exportData.footer.forEach(function (cell, i) {
        parts.push('<th' + moneyClass(i) + '>', escapeHtml(cell), '</th>');
      });
      parts.push('</tr></tfoot>');
    }
    parts.push('</table>');
    if (bankCtx) {
      parts.push(
        '<div class="bank-footer"><span>This is a computer-generated statement and does not require a signature.</span><span>Canmee Dairies (Pvt) Ltd · Confidential</span></div>'
      );
    }
    parts.push('</body></html>');
    var html = parts.join('');

    var iframe = document.createElement('iframe');
    iframe.setAttribute('aria-hidden', 'true');
    iframe.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden;';
    document.body.appendChild(iframe);

    var printWin = iframe.contentWindow;
    var printDoc = printWin.document;
    printDoc.open();
    printDoc.write(html);
    printDoc.close();

    function doPrint() {
      try {
        printWin.focus();
        printWin.print();
      } catch (err) {
        console.error('DataTable print failed', err);
      }
      window.setTimeout(function () {
        if (iframe.parentNode) {
          iframe.parentNode.removeChild(iframe);
        }
      }, 1000);
    }

    if (printDoc.readyState === 'complete') {
      window.setTimeout(doPrint, 100);
    } else {
      iframe.onload = function () {
        window.setTimeout(doPrint, 100);
      };
      window.setTimeout(doPrint, 500);
    }
  }

  /** Map responsive classes on <th> into DataTables columnDefs (required for Responsive 3). */
  function buildResponsiveColumnDefs(tableEl) {
    if (!tableEl || !tableEl.tHead || !tableEl.tHead.rows.length) return [];
    var headers = tableEl.tHead.rows[0].cells;
    var byTarget = {};
    function ensure(index) {
      if (!byTarget[index]) byTarget[index] = { targets: index };
      return byTarget[index];
    }
    for (var i = 0; i < headers.length; i++) {
      var cls = ' ' + (headers[i].className || '') + ' ';
      var def = ensure(i);
      if (cls.indexOf(' none ') !== -1) {
        def.className = 'none';
        def.responsivePriority = 10000;
      } else if (cls.indexOf(' all ') !== -1) {
        def.className = 'all';
        def.responsivePriority = 1;
      } else if (cls.indexOf(' min-desktop ') !== -1) {
        def.className = 'min-desktop';
        def.responsivePriority = 100;
      } else if (cls.indexOf(' min-tablet-l ') !== -1) {
        def.className = 'min-tablet-l';
        def.responsivePriority = 60;
      } else if (cls.indexOf(' min-tablet ') !== -1) {
        def.className = 'min-tablet';
        def.responsivePriority = 50;
      } else if (cls.indexOf(' min-phone-l ') !== -1) {
        def.className = 'min-phone-l';
        def.responsivePriority = 30;
      }
    }
    return Object.keys(byTarget).map(function (key) {
      return byTarget[key];
    });
  }

  /** On narrow screens keep only listed column indexes visible (0-based); restores all on desktop. */
  function bindMobileColumnLayout(tableEl, api) {
    var spec = tableEl.getAttribute('data-mobile-visible-cols');
    if (!spec || !api) return;
    var keep = spec
      .split(',')
      .map(function (part) {
        return parseInt(part.trim(), 10);
      })
      .filter(function (n) {
        return !isNaN(n);
      });
    if (!keep.length) return;

    var colCount = api.columns().count();
    var resizeTimer;

    function sync() {
      var mobile = window.innerWidth < 768;
      for (var i = 0; i < colCount; i++) {
        var show = !mobile || keep.indexOf(i) !== -1;
        api.column(i).visible(show, false);
      }
      api.columns.adjust();
      if (api.responsive && typeof api.responsive.recalc === 'function') {
        api.responsive.recalc();
      }
    }

    sync();
    window.addEventListener('resize', function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(sync, 120);
    });
  }

  $(function () {
    initSidebar();
    initSidebarGroups();
    flushDjangoMessages();
    flushBootstrapAlertsToSwal();
    initSwalDeletePosts();
    initSwalSubmitTriggers();
    initSwalSettlementTriggers();
    initDtExportMenuPin();

    if ($.fn.DataTable) {
      $('.datatable').each(function () {
        var el = this;
        var DT = $.fn.DataTable;
        if (DT && DT.isDataTable && DT.isDataTable(el)) return;
        var hasButtons = !!($.fn.dataTable && $.fn.dataTable.Buttons);
        var hasResponsive = !!($.fn.dataTable && $.fn.dataTable.Responsive);
        var disableResponsive = el.classList.contains('datatable-no-responsive');
        var pageLengthAttr = el.getAttribute('data-page-length');
        var showAllRows = pageLengthAttr === 'all';
        var pageLength = 25;
        if (pageLengthAttr && !showAllRows) {
          var parsedPageLength = parseInt(pageLengthAttr, 10);
          if (!isNaN(parsedPageLength)) pageLength = parsedPageLength;
        }
        var exportColumns = getExportColumnIndexes(el);
        var exportFormat = { body: exportBodyText, footer: exportBodyText };
        var isSmartTable = el.classList.contains('datatable-smart');
        var responsiveColumnDefs = buildResponsiveColumnDefs(el);
        var hasSequenceCol = tableHasSequenceColumn(el);
        if (hasSequenceCol) {
          responsiveColumnDefs.push(sequenceColumnDef(el));
        }
        var responsiveDetailsTarget = 'tr';
        if (el.classList.contains('branch-table')) {
          responsiveDetailsTarget = 1;
          if (isSmartTable) {
            responsiveColumnDefs.push(
              { targets: [2, 3, 4, 6], className: 'text-end dt-num' },
              { targets: 5, orderable: false }
            );
          }
        } else if (el.classList.contains('route-table')) {
          responsiveDetailsTarget = 1;
          if (isSmartTable) {
            responsiveColumnDefs.push(
              { targets: [5], className: 'text-end dt-num' }
            );
          }
        } else if (el.classList.contains('farmer-table')) {
          responsiveDetailsTarget = 1;
        } else if (el.classList.contains('point-table')) {
          responsiveDetailsTarget = 1;
          if (isSmartTable) {
            responsiveColumnDefs.push(
              { targets: [5, 6], className: 'text-end dt-num' }
            );
          }
        } else if (el.classList.contains('raw-milk-table')) {
          responsiveDetailsTarget = 0;
        } else if (el.classList.contains('buyer-dispatch-table')) {
          responsiveDetailsTarget = 0;
          if (isSmartTable) {
            responsiveColumnDefs.push(
              {
                targets: 0,
                className: 'text-start dt-date all',
                type: 'string',
              },
              { targets: [6, 7, 8, 9], className: 'text-end dt-num' }
            );
          }
        } else if (el.classList.contains('dispatch-table')) {
          var hasDispatchSelectCol = !!(
            el.tHead &&
            el.tHead.rows[0] &&
            el.tHead.rows[0].cells[0] &&
            el.tHead.rows[0].cells[0].querySelector('input[type="checkbox"]')
          );
          responsiveDetailsTarget = hasDispatchSelectCol ? 1 : 0;
          if (isSmartTable) {
            if (hasDispatchSelectCol) {
              responsiveColumnDefs.push({
                targets: 0,
                orderable: false,
                searchable: false,
                className: 'dispatch-select-col all',
              });
            }
            responsiveColumnDefs.push({
              targets: -1,
              orderable: false,
              searchable: false,
            });
          }
        } else if (el.classList.contains('rms-collection-table')) {
          responsiveDetailsTarget = 0;
          if (isSmartTable) {
            responsiveColumnDefs.push(
              {
                targets: 0,
                className: 'text-start dt-date all',
                type: 'string',
              },
              { targets: [5, 6, 7, 8], className: 'text-end dt-num' }
            );
          }
        } else if (el.classList.contains('loan-table')) {
          responsiveDetailsTarget = 2;
        } else if (el.classList.contains('asset-table')) {
          responsiveDetailsTarget = 3;
          if (isSmartTable) {
            responsiveColumnDefs.push(
              { targets: 0, orderable: false, searchable: false },
              { targets: -1, orderable: false, searchable: false }
            );
          }
        } else if (el.classList.contains('point-reconcile-table')) {
          responsiveDetailsTarget = 0;
        } else if (el.classList.contains('user-table')) {
          responsiveDetailsTarget = 0;
        } else if (el.classList.contains('pending-accept-table')) {
          // Control on Point column (index 4): #, checkbox, date, issued, point...
          responsiveDetailsTarget = 4;
          if (isSmartTable) {
            responsiveColumnDefs.push(
              { targets: 1, orderable: false, searchable: false },
              { targets: -1, orderable: false, searchable: false },
              { targets: [8, 9], className: 'text-end dt-num' }
            );
          } else {
            responsiveColumnDefs.push(
              { targets: [0, 1], orderable: false, searchable: false },
              { targets: -1, orderable: false, searchable: false },
              { targets: [8, 9], className: 'text-end dt-num' }
            );
          }
        } else if (el.classList.contains('farmer-milk-collections-table')) {
          var hasMilkSelectCol = !!(el.tHead && el.tHead.rows[0] && el.tHead.rows[0].cells[0] && el.tHead.rows[0].cells[0].querySelector('input[type="checkbox"]'));
          responsiveDetailsTarget = hasMilkSelectCol ? 1 : 0;
          if (isSmartTable) {
            if (hasMilkSelectCol) {
              responsiveColumnDefs.push({
                targets: 0,
                orderable: false,
                searchable: false,
                width: '36px',
                className: 'farmer-milk-select-col all',
              });
            }
            responsiveColumnDefs.push(
              { targets: -1, orderable: false, searchable: false },
              { targets: hasMilkSelectCol ? [3, 4] : [2, 3], className: 'text-end dt-num' }
            );
          }
        } else if (
          el.classList.contains('buyer-account-pay-table') ||
          el.classList.contains('rms-account-pay-table')
        ) {
          responsiveDetailsTarget = 1;
          if (isSmartTable) {
            responsiveColumnDefs.push(
              { targets: 0, orderable: false, searchable: false, className: 'all' },
              { targets: 1, className: 'text-start dt-date all', type: 'string' },
              { targets: 2, className: 'text-start all', type: 'string' },
              { targets: [4, 5, 6], className: 'text-end dt-num' },
              { targets: -1, orderable: false, searchable: false }
            );
          }
        } else if (
          el.classList.contains('buyer-account-ledger-table') ||
          el.classList.contains('rms-account-ledger-table')
        ) {
          if (isSmartTable) {
            responsiveColumnDefs.push(
              { targets: 0, className: 'text-start dt-date all', type: 'string' },
              // Keep chronological order as the only sort so Balance stays checkable.
              { targets: [1, 2, 4, 5], orderable: false },
              { targets: 2, className: 'text-end dt-num' },
              { targets: 3, className: 'text-end dt-num', orderable: false },
              { targets: 4, className: 'text-start dt-ref' }
            );
          }
        }
        var isFarmerMilkTable = el.classList.contains('farmer-milk-collections-table');
        var isPointGoodsIssuesTable = el.classList.contains('point-goods-issues-table');
        var isPointGoodsSummaryTable = el.classList.contains('point-goods-summary-table');
        var isPointFinanceFitTable =
          isPointGoodsIssuesTable ||
          isPointGoodsSummaryTable ||
          el.classList.contains('point-finance-fit-table');
        function fitFarmerMilkTable(api) {
          if (!isFarmerMilkTable || !api) return;
          var tableNode = api.table().node();
          if (!tableNode) return;
          tableNode.style.setProperty('width', '100%', 'important');
          var selectHeader = tableNode.querySelector('th.farmer-milk-select-col');
          var hasSelect = !!selectHeader;
          var cols = tableNode.querySelectorAll('colgroup col');
          var lastIndex = cols.length - 1;
          Array.prototype.forEach.call(cols, function (col, index) {
            col.style.removeProperty('min-width');
            col.style.removeProperty('max-width');
            if (hasSelect && index === 0) {
              col.classList.add('farmer-milk-select-col');
              col.style.setProperty('width', '36px', 'important');
              col.style.setProperty('min-width', '36px', 'important');
              col.style.setProperty('max-width', '36px', 'important');
              return;
            }
            if (index === lastIndex) {
              col.style.setProperty('width', '2.75rem', 'important');
              col.style.setProperty('min-width', '2.75rem', 'important');
              col.style.setProperty('max-width', '2.75rem', 'important');
              return;
            }
            col.style.removeProperty('width');
            col.style.width = '';
          });
          try {
            api.columns.adjust();
          } catch (e) {}
        }
        function pinPointFinanceToolbar(api) {
          var container = api && api.table().container();
          if (!container) return;
          var pane = container.closest('.point-finance-pane');
          if (!pane) return;
          var tableNode = api.table().node();
          var wrap = tableNode ? tableNode.closest('.point-finance-pane__table') : null;
          var title = null;
          if (wrap) {
            var prev = wrap.previousElementSibling;
            while (prev) {
              if (prev.classList && prev.classList.contains('point-finance-pane__title')) {
                title = prev;
                break;
              }
              prev = prev.previousElementSibling;
            }
          }
          if (!title) title = pane.querySelector(':scope > .point-finance-pane__title');
          if (!title) return;
          var tools = title.querySelector('.point-finance-pane__tools');
          if (!tools) {
            tools = document.createElement('span');
            tools.className = 'point-finance-pane__tools';
            title.appendChild(tools);
          }
          var search = container.querySelector('.dt-search');
          var buttons = container.querySelector('.dt-buttons');
          if (search && !tools.contains(search)) tools.appendChild(search);
          if (buttons && !tools.contains(buttons)) tools.appendChild(buttons);
          var leftover = container.querySelector('.dt-smart-toolbar');
          if (leftover && !leftover.querySelector('.dt-search, .dt-buttons, .dt-smart-filters-wrap:not(:empty)')) {
            leftover.style.display = 'none';
          }
        }
        function fitPointGoodsIssuesTable(api) {
          if (!isPointFinanceFitTable || !api) return;
          var tableNode = api.table().node();
          if (!tableNode) return;
          tableNode.style.setProperty('width', '100%', 'important');
          tableNode.style.setProperty('max-width', '100%', 'important');
          var container = api.table().container();
          if (container) {
            container.style.setProperty('width', '100%', 'important');
            container.style.setProperty('max-width', '100%', 'important');
          }
          Array.prototype.forEach.call(tableNode.querySelectorAll('colgroup col'), function (col) {
            col.style.removeProperty('width');
            col.style.removeProperty('min-width');
            col.style.removeProperty('max-width');
            col.style.width = '';
          });
        }
        var emptyTableMessage = el.getAttribute('data-empty-table');
        var bottomLayout =
          "<'row g-2 align-items-center mt-2 dt-bottom-row'<'col-md-4 dt-bottom-length'l><'col-md-4 dt-bottom-info'i><'col-md-4 dt-bottom-pagination'p>>";
        var disableSearch = el.getAttribute("data-disable-search") === "true";
        var smartDom = disableSearch
          ? "<'row g-2 align-items-center mb-2 dt-smart-toolbar'<'col-12 d-flex flex-wrap gap-2 justify-content-between align-items-center'<'dt-smart-filters-wrap'><'dt-smart-actions d-flex flex-wrap gap-2 align-items-center'B>>>"
          : "<'row g-2 align-items-center mb-2 dt-smart-toolbar'<'col-12 d-flex flex-wrap gap-2 justify-content-between align-items-center'<'dt-smart-filters-wrap'><'dt-smart-actions d-flex flex-wrap gap-2 align-items-center'fB>>>";
        var defaultDom = disableSearch
          ? "<'row g-2 align-items-center mb-2'<'col-12 d-flex gap-2 align-items-center'B>>"
          : "<'row g-2 align-items-center mb-2'<'col-sm-6 d-flex gap-2 align-items-center'B><'col-sm-6'f>>";
        var numericFooterCols = (el.getAttribute('data-footer-sum-cols') || '')
          .split(',')
          .map(function (part) {
            return parseInt(part.trim(), 10);
          })
          .filter(function (n) {
            return !isNaN(n);
          });
        var smartOrderAttr = el.getAttribute('data-default-order-col');
        var smartOrderCol = smartOrderAttr ? parseInt(smartOrderAttr, 10) : 1;
        if (isNaN(smartOrderCol)) smartOrderCol = 1;
        // Use data-smart-order (not data-order): DataTables treats data-order on <table> as
        // a native init option and rejects values like "2,desc".
        var defaultOrderAttr = el.getAttribute('data-smart-order');
        var initialOrder = isSmartTable ? [[smartOrderCol, 'asc']] : [];
        if (defaultOrderAttr) {
          // e.g. data-smart-order="1,desc"
          var orderParts = defaultOrderAttr.split(',');
          var orderCol = parseInt(orderParts[0], 10);
          var orderDir = (orderParts[1] || 'asc').trim().toLowerCase() === 'desc' ? 'desc' : 'asc';
          if (!isNaN(orderCol)) initialOrder = [[orderCol, orderDir]];
        }
        var isBuyerLedgerTable =
          el.classList.contains('buyer-account-ledger-table') ||
          el.classList.contains('rms-account-ledger-table');
        var ledgerRowCount = isBuyerLedgerTable ? el.querySelectorAll('tbody tr').length : 0;
        var ledgerDisplayStart =
          isBuyerLedgerTable && !showAllRows && ledgerRowCount > pageLength
            ? Math.max(0, ledgerRowCount - pageLength)
            : 0;
        var options = {
          pageLength: pageLength,
          paging: !showAllRows,
          lengthChange: !showAllRows,
          searching: !disableSearch,
          info: true,
          lengthMenu: [10, 25, 50, 100],
          pagingType: isSmartTable ? 'first_last_numbers' : 'simple_numbers',
          order: initialOrder,
          displayStart: ledgerDisplayStart,
          autoWidth: isFarmerMilkTable,
          columnDefs: responsiveColumnDefs,
          responsive:
            !disableResponsive && hasResponsive
              ? {
                  details: {
                    type: 'inline',
                    target: responsiveDetailsTarget,
                  },
                }
              : false,
          dom: (isSmartTable ? smartDom : defaultDom) + 't' + bottomLayout,
          buttons: hasButtons
            ? isSmartTable
              ? buildSmartExportButtons(el, exportColumns, exportFormat, document.title || 'Report')
              : buildLegacyExportButtons(exportColumns, exportFormat)
            : [],
          footerCallback: isSmartTable && numericFooterCols.length && el.tFoot
            ? function () {
                smartFooterTotals(this.api(), numericFooterCols);
              }
            : undefined,
          drawCallback: function () {
            var api = this.api();
            if (hasSequenceCol) {
              refreshSequenceNumbers(api);
            }
            fitFarmerMilkTable(api);
            fitPointGoodsIssuesTable(api);
          },
          initComplete: isSmartTable
            ? function () {
                var api = this.api();
                if (hasSequenceCol) {
                  refreshSequenceNumbers(api);
                }
                var container = $(api.table().container());
                container.addClass('dt-smart-table');
                var filters = document.getElementById(el.getAttribute('data-filters-target'));
                if (filters) {
                  container.find('.dt-smart-filters-wrap').append(filters);
                  filters.classList.remove('d-none');
                }
                var tabs = document.getElementById(el.getAttribute('data-tabs-target'));
                if (tabs) {
                  var actions = container.find('.dt-smart-actions');
                  if (actions.length) {
                    actions.prepend(tabs);
                    tabs.classList.remove('d-none');
                  }
                }
                bindSmartTableFilters(api, el);
                pinPointFinanceToolbar(api);
                fitFarmerMilkTable(api);
                fitPointGoodsIssuesTable(api);
              }
            : hasSequenceCol
              ? function () {
                  refreshSequenceNumbers(this.api());
                }
              : isFarmerMilkTable
                ? function () {
                    fitFarmerMilkTable(this.api());
                  }
                : undefined,
          language: {
            search: '',
            searchPlaceholder: 'Search…',
            lengthMenu: 'Show _MENU_ rows',
            info: showAllRows ? '_TOTAL_ records' : 'Showing _START_–_END_ of _TOTAL_',
            infoEmpty: 'No rows',
            infoFiltered: '(filtered from _MAX_)',
            zeroRecords: 'No matching records',
            emptyTable: emptyTableMessage || 'No records yet',
            paginate: {
              first: '<i class="bi bi-chevron-double-left"></i>',
              previous: '<i class="bi bi-chevron-left"></i>',
              next: '<i class="bi bi-chevron-right"></i>',
              last: '<i class="bi bi-chevron-double-right"></i>',
            },
          },
        };
        try {
          var api = $(el).DataTable(options);
          bindMobileColumnLayout(el, api);
        } catch (err) {
          console.error('DataTable init failed for', el, err);
        }
      });
    }
  });
})();

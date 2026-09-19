(function () {
  var FEED_URL = "/dispatch/notifications/feed/";
  var POLL_MS = 15000;
  var pollTimer = null;

  var modalEl = document.getElementById("branchDispatchRespondModal");
  var contentEl = document.getElementById("branchDispatchRespondModalContent");
  var modal = modalEl && contentEl && window.bootstrap
    ? bootstrap.Modal.getOrCreateInstance(modalEl)
    : null;

  function getCsrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function getNotificationsBody() {
    return document.getElementById("dispatchNotificationsBody");
  }

  function goodsAndRequestUnreadFromDom() {
    var goodsEl = document.querySelector(".js-staff-goods-notif-count");
    var requestEl = document.querySelector(".js-driver-request-notif-count");
    var goods = goodsEl ? parseInt(goodsEl.textContent, 10) : 0;
    var requests = requestEl ? parseInt(requestEl.textContent, 10) : 0;
    if (!isFinite(goods) || goods < 0) goods = 0;
    if (!isFinite(requests) || requests < 0) requests = 0;
    return goods + requests;
  }

  function updateNotificationBadge(dispatchUnread) {
    var toggle = document.querySelector(".topbar-notifications-toggle");
    if (!toggle) return;
    var dispatchCount = Number(dispatchUnread) || 0;
    if (dispatchCount < 0) dispatchCount = 0;
    var total = dispatchCount + goodsAndRequestUnreadFromDom();
    var badge = toggle.querySelector(".topbar-notifications-badge");
    if (total > 0) {
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "topbar-notifications-badge js-topbar-notif-badge";
        toggle.appendChild(badge);
      } else if (!badge.classList.contains("js-topbar-notif-badge")) {
        badge.classList.add("js-topbar-notif-badge");
      }
      badge.hidden = false;
      badge.textContent = String(total);
    } else if (badge) {
      badge.hidden = true;
      badge.textContent = "0";
    }
    var markAllForm = document.querySelector(".js-dispatch-notifications-mark-all");
    if (markAllForm) {
      markAllForm.style.display = dispatchCount > 0 ? "" : "none";
    }
  }

  function renderNotificationItem(note) {
    var linkHtml = "";
    if (note.link_href) {
      linkHtml =
        '<a href="' +
        escapeHtml(note.link_href) +
        '" class="topbar-notification-item__link js-dispatch-notification-modal" data-modal-type="' +
        escapeHtml(note.modal_type || "list") +
        '">' +
        '<div class="topbar-notification-item__title">' +
        escapeHtml(note.title) +
        "</div>" +
        '<div class="topbar-notification-item__body">' +
        escapeHtml(note.body) +
        "</div></a>";
    } else {
      linkHtml =
        '<div class="topbar-notification-item__title">' +
        escapeHtml(note.title) +
        "</div>" +
        '<div class="topbar-notification-item__body">' +
        escapeHtml(note.body) +
        "</div>";
    }
    var metaOpen = "";
    if (note.meta_href) {
      metaOpen =
        '<a href="' +
        escapeHtml(note.meta_href) +
        '" class="ms-2 js-dispatch-notification-modal" data-modal-type="' +
        escapeHtml(note.modal_type || "list") +
        '">' +
        escapeHtml(note.meta_label || "Open") +
        "</a>";
    }
    var markRead =
      '<form method="post" action="' +
      escapeHtml(note.read_url) +
      '" class="d-inline ms-2">' +
      '<input type="hidden" name="csrfmiddlewaretoken" value="' +
      escapeHtml(getCsrfToken()) +
      '">' +
      '<input type="hidden" name="next" value="' +
      escapeHtml(window.location.pathname + window.location.search) +
      '">' +
      '<button type="submit" class="btn btn-link btn-sm p-0">Mark read</button></form>';
    return (
      '<div class="topbar-notification-item is-unread" data-notification-id="' +
      escapeHtml(note.id) +
      '">' +
      linkHtml +
      '<div class="topbar-notification-item__meta">' +
      "<span>" +
      escapeHtml(note.created_ago) +
      "</span>" +
      metaOpen +
      markRead +
      "</div></div>"
    );
  }

  function renderNotificationsList(notifications) {
    var body = getNotificationsBody();
    if (!body) return;
    if (!notifications || !notifications.length) {
      body.innerHTML =
        '<div class="topbar-notifications-empty text-muted small" id="dispatchNotificationsEmpty">No notifications yet.</div>';
      return;
    }
    body.innerHTML =
      '<div class="topbar-notifications-list" id="dispatchNotificationsList">' +
      notifications.map(renderNotificationItem).join("") +
      "</div>";
  }

  function refreshDispatchNotifications(forceList) {
    if (!document.querySelector(".topbar-notifications-dropdown")) return Promise.resolve();
    return fetch(FEED_URL, {
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (response) {
        if (!response.ok) throw new Error("feed failed");
        return response.json();
      })
      .then(function (data) {
        if (!data || !data.ok) return;
        updateNotificationBadge(data.unread || 0);
        renderNotificationsList(data.notifications || []);
      })
      .catch(function () {});
  }

  function startNotificationPolling() {
    if (pollTimer) return;
    pollTimer = window.setInterval(function () {
      refreshDispatchNotifications(false);
    }, POLL_MS);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") {
        refreshDispatchNotifications(true);
      }
    });
  }

  window.refreshDispatchNotifications = refreshDispatchNotifications;

  function ensureNotificationsEmptyState() {
    var body = getNotificationsBody();
    if (!body) return;
    if (body.querySelector(".topbar-notification-item")) return;
    body.innerHTML =
      '<div class="topbar-notifications-empty text-muted small" id="dispatchNotificationsEmpty">No notifications yet.</div>';
  }

  function markNotificationRead(notificationId, itemEl) {
    if (!notificationId || !itemEl) return;
    var body = new FormData();
    fetch("/dispatch/notifications/" + notificationId + "/read/", {
      method: "POST",
      body: body,
      credentials: "same-origin",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": getCsrfToken(),
      },
    })
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        if (!data.ok) return;
        itemEl.remove();
        updateNotificationBadge(data.unread || 0);
        ensureNotificationsEmptyState();
      })
      .catch(function () {});
  }

  function embedUrl(url) {
    var sep = url.indexOf("?") >= 0 ? "&" : "?";
    return url + sep + "embed=1";
  }

  function extractErrors(data) {
    var messages = [];
    var errors = (data && data.errors) || {};
    Object.keys(errors).forEach(function (key) {
      (errors[key] || []).forEach(function (msg) {
        if (msg) messages.push(msg);
      });
    });
    return messages;
  }

  function showErrors(root, selector, messages) {
    var errorsEl = root.querySelector(selector);
    if (!errorsEl) return;
    if (!messages.length) {
      errorsEl.classList.add("d-none");
      errorsEl.textContent = "";
      return;
    }
    errorsEl.classList.remove("d-none");
    errorsEl.textContent = messages.join(" ");
  }

  function syncRespondFields(root) {
    var value = root.querySelector('input[name="response_action"]:checked');
    var action = value ? value.value : "collect";
    root.querySelectorAll(".branch-respond-field--quantity").forEach(function (el) {
      el.style.display = "";
    });
    root.querySelectorAll(".branch-respond-field--divert-branch").forEach(function (el) {
      el.style.display = action === "divert_branch" ? "" : "none";
    });
    root.querySelectorAll(".branch-respond-field--divert-buyer").forEach(function (el) {
      el.style.display = action === "divert_buyer" ? "" : "none";
    });
    var labelEl = root.querySelector("#branch-return-qty-label");
    var unitEl = root.querySelector("#id_return_quantity_unit");
    var unit = (unitEl && unitEl.value) || "liters";
    if (labelEl) {
      var unitWord = unit === "liters" ? "liters" : "kg";
      var prefix = "Quantity";
      if (action === "return") prefix = "Return quantity";
      else if (action === "collect") prefix = "Collected quantity";
      else if (action === "divert_branch" || action === "divert_buyer") prefix = "Divert quantity";
      labelEl.textContent = prefix + " (" + unitWord + ")";
    }
  }

  function formatQty2(n) {
    return (Math.round(n * 100) / 100).toFixed(2);
  }

  function initRespondQuantityToggle(root) {
    var wrap = root.querySelector("#branch-return-qty-wrap");
    if (!wrap || wrap.dataset.qtyToggleBound === "1") return;
    wrap.dataset.qtyToggleBound = "1";

    var qtyEl = wrap.querySelector("#id_return_quantity");
    var unitEl = wrap.querySelector("#id_return_quantity_unit");
    var availableEl = wrap.querySelector("#branch-return-available-qty");
    var kgToL = Number(wrap.dataset.kgToLiters || "0");
    var availableKg = Number(wrap.dataset.kg || "0");
    var availableLiters = Number(wrap.dataset.liters || "0");
    var currentUnit = (unitEl && unitEl.value) || "liters";

    function applyChrome(unit) {
      currentUnit = unit;
      if (unitEl) unitEl.value = unit;
      wrap.querySelectorAll(".qty-segment__btn[data-qty-unit]").forEach(function (btn) {
        var on = btn.getAttribute("data-qty-unit") === unit;
        btn.classList.toggle("is-active", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
      });
      syncRespondFields(root);
      if (qtyEl) {
        if (unit === "liters" && availableLiters) qtyEl.max = formatQty2(availableLiters);
        else if (unit === "kg" && availableKg) qtyEl.max = formatQty2(availableKg);
        else qtyEl.removeAttribute("max");
      }
      if (availableEl) {
        availableEl.textContent =
          "Available: " +
          formatQty2(availableLiters) +
          " L (" +
          formatQty2(availableKg) +
          " kg)";
      }
    }

    function switchUnit(next) {
      if (!qtyEl || next === currentUnit) return;
      var n = parseFloat(String(qtyEl.value || "").replace(",", "."));
      if (!isNaN(n) && kgToL) {
        if (currentUnit === "kg" && next === "liters") {
          qtyEl.value = formatQty2(n * kgToL);
        } else if (currentUnit === "liters" && next === "kg") {
          qtyEl.value = formatQty2(n / kgToL);
        }
      }
      applyChrome(next);
    }

    wrap.querySelectorAll(".qty-segment__btn[data-qty-unit]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        switchUnit(btn.getAttribute("data-qty-unit"));
      });
    });
    applyChrome(currentUnit);
  }

  function initSelect2(root) {
    if (!window.jQuery || !window.jQuery.fn.select2 || !modalEl) return;
    var $ = window.jQuery;
    var $modal = $(modalEl);
    root.querySelectorAll("select").forEach(function (select) {
      var $el = $(select);
      if ($el.hasClass("select2-hidden-accessible")) {
        $el.select2("destroy");
      }
      $el.select2({
        width: "100%",
        dropdownParent: $modal,
        placeholder: "Select option",
      });
    });
  }

  function bindAjaxForm(root, options) {
    var form = root.querySelector(options.formSelector);
    if (!form || form.dataset.bound === "1") return;
    form.dataset.bound = "1";

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      showErrors(root, options.errorSelector, []);
      var submitBtn = form.querySelector('[type="submit"]');
      if (submitBtn) submitBtn.disabled = true;

      fetch(form.getAttribute("action"), {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then(function (response) {
          var contentType = response.headers.get("content-type") || "";
          if (contentType.indexOf("application/json") === -1) {
            throw new Error("Unexpected response");
          }
          return response.json().then(function (data) {
            return { ok: response.ok, data: data };
          });
        })
        .then(function (result) {
          if (result.ok && result.data && result.data.ok) {
            if (modal) modal.hide();
            refreshDispatchNotifications(true);
            window.location.href = result.data.redirect || options.fallbackRedirect;
            return;
          }
          var messages = extractErrors(result.data);
          if (!messages.length) messages.push(options.errorMessage);
          showErrors(root, options.errorSelector, messages);
        })
        .catch(function () {
          showErrors(root, options.errorSelector, [options.errorMessage + " Please try again."]);
        })
        .finally(function () {
          if (submitBtn) submitBtn.disabled = false;
        });
    });
  }

  function bindLoadedContent(root) {
    if (root.querySelector("#branchDispatchRespondForm")) {
      syncRespondFields(root);
      initRespondQuantityToggle(root);
      root.querySelectorAll('input[name="response_action"]').forEach(function (input) {
        input.addEventListener("change", function () {
          syncRespondFields(root);
        });
      });
      bindAjaxForm(root, {
        formSelector: "#branchDispatchRespondForm",
        errorSelector: "#branchDispatchRespondErrors",
        fallbackRedirect: "/dispatch/?filter_tab=branch&branch_scope=incoming",
        errorMessage: "Could not save response.",
      });
    }
    if (root.querySelector("#branchDispatchReturnForm")) {
      bindAjaxForm(root, {
        formSelector: "#branchDispatchReturnForm",
        errorSelector: "#branchDispatchReturnErrors",
        fallbackRedirect: "/dispatch/?filter_tab=branch&branch_scope=outgoing",
        errorMessage: "Could not save decision.",
      });
    }
  }

  function isBranchRespondUrl(url) {
    return /\/dispatch\/\d+\/branch-respond\/?/.test(url || "");
  }

  function isBranchReturnUrl(url) {
    return /\/dispatch\/\d+\/branch-return\/?/.test(url || "");
  }

  function resolveModalType(trigger, href) {
    var type = trigger.getAttribute("data-modal-type");
    if (type) return type;
    if (isBranchRespondUrl(href)) return "respond";
    if (isBranchReturnUrl(href)) return "return";
    return "list";
  }

  function closeNotificationsDropdown() {
    var toggle = document.querySelector(".topbar-notifications-toggle");
    if (!toggle || !window.bootstrap) return;
    var dropdown = bootstrap.Dropdown.getInstance(toggle);
    if (dropdown) dropdown.hide();
  }

  function showLoading() {
    if (!contentEl) return;
    contentEl.innerHTML =
      '<div class="modal-body py-5 text-center text-muted">' +
      '<div class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></div>' +
      "Loading…</div>";
    if (modal) modal.show();
  }

  function openInfoModal(trigger, href) {
    closeNotificationsDropdown();
    var item = trigger.closest(".topbar-notification-item");
    var titleEl = item ? item.querySelector(".topbar-notification-item__title") : null;
    var bodyEl = item ? item.querySelector(".topbar-notification-item__body") : null;
    var title = titleEl ? titleEl.textContent.trim() : "Notification";
    var body = bodyEl ? bodyEl.textContent.trim() : "";
    if (!contentEl) {
      window.location.href = href;
      return;
    }
    contentEl.innerHTML =
      '<div class="modal-header">' +
      '<h5 class="modal-title">' + escapeHtml(title) + "</h5>" +
      '<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>' +
      "</div>" +
      '<div class="modal-body"><p class="mb-0">' + escapeHtml(body) + "</p></div>" +
      '<div class="modal-footer">' +
      '<button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Close</button>' +
      '<a href="' + escapeHtml(href) + '" class="btn btn-primary">Open dispatch list</a>' +
      "</div>";
    if (modal) modal.show();
  }

  function openFetchedModal(url) {
    if (!contentEl || !modal) {
      window.location.href = url;
      return;
    }
    closeNotificationsDropdown();
    showLoading();
    fetch(embedUrl(url), {
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (response) {
        if (!response.ok) throw new Error("load failed");
        return response.text();
      })
      .then(function (html) {
        contentEl.innerHTML = html;
        initSelect2(contentEl);
        bindLoadedContent(contentEl);
      })
      .catch(function () {
        contentEl.innerHTML =
          '<div class="modal-body"><div class="alert alert-danger mb-0">Could not load notification form.</div></div>';
      });
  }

  function openNotificationModal(trigger, href) {
    var item = trigger.closest(".topbar-notification-item");
    var notificationId = item ? item.getAttribute("data-notification-id") : null;
    if (notificationId && item && item.classList.contains("is-unread")) {
      markNotificationRead(notificationId, item);
    }
    var type = resolveModalType(trigger, href);
    if (type === "list") {
      openInfoModal(trigger, href);
      return;
    }
    openFetchedModal(href);
  }

  document.addEventListener("click", function (e) {
    var trigger = e.target.closest("a[href].js-dispatch-notification-modal, a[href].js-branch-dispatch-respond-modal");
    if (!trigger) return;
    var href = trigger.getAttribute("href");
    if (!href) return;
    e.preventDefault();
    openNotificationModal(trigger, href);
  });

  document.addEventListener("submit", function (e) {
    var form = e.target.closest(".js-dispatch-notifications-mark-all");
    if (form) {
      e.preventDefault();
      var submitBtn = form.querySelector('[type="submit"]');
      if (submitBtn) submitBtn.disabled = true;
      fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": getCsrfToken(),
        },
      })
        .then(function (response) {
          return response.json();
        })
        .then(function (data) {
          if (!data.ok) return;
          updateNotificationBadge(0);
          renderNotificationsList([]);
        })
        .catch(function () {})
        .finally(function () {
          if (submitBtn) submitBtn.disabled = false;
        });
      return;
    }

    var itemForm = e.target.closest(".topbar-notification-item form");
    if (!itemForm || !itemForm.action || itemForm.action.indexOf("/dispatch/notifications/") === -1) return;
    e.preventDefault();
    var item = itemForm.closest(".topbar-notification-item");
    var notificationId = item ? item.getAttribute("data-notification-id") : null;
    if (!notificationId) return;
    var submitBtn = itemForm.querySelector('[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;
    fetch(itemForm.action, {
      method: "POST",
      body: new FormData(itemForm),
      credentials: "same-origin",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": getCsrfToken(),
      },
    })
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        if (!data.ok) return;
        if (item) item.remove();
        updateNotificationBadge(data.unread || 0);
        ensureNotificationsEmptyState();
      })
      .catch(function () {})
      .finally(function () {
        if (submitBtn) submitBtn.disabled = false;
      });
  });

  if (modalEl && contentEl) {
    modalEl.addEventListener("hidden.bs.modal", function () {
      contentEl.innerHTML =
        '<div class="modal-body py-5 text-center text-muted">' +
        '<div class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></div>' +
        "Loading…</div>";
    });
  }

  if (document.querySelector(".topbar-notifications-dropdown")) {
    startNotificationPolling();
    refreshDispatchNotifications(true);
  }
})();

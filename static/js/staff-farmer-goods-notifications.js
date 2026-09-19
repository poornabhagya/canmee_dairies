(function () {
  var FEED_URL = "/suppliers/farmer-goods/notifications/feed/";
  var POLL_MS = 12000;
  var KNOWN_REFS_KEY = "staffGoodsKnownBatchRefs";
  var KNOWN_REQUEST_IDS_KEY = "staffGoodsKnownRequestIds";
  var PERM_PROMPT_KEY = "staffGoodsNotifPrompted";
  var pollTimer = null;
  var itemsByRef = {};
  var knownRefs = loadKnownRefs();
  var knownRequestIds = loadKnownRequestIds();
  var feedReady = false;
  var iconUrl = "/static/pwa/app-icon.png";
  var dispatchUnread = countDispatchUnread();

  function getCsrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (match) return decodeURIComponent(match[1]);
    var input = document.querySelector("[name=csrfmiddlewaretoken]");
    return input ? input.value : "";
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function loadKnownRefs() {
    try {
      var raw = sessionStorage.getItem(KNOWN_REFS_KEY);
      var parsed = raw ? JSON.parse(raw) : [];
      return new Set(Array.isArray(parsed) ? parsed : []);
    } catch (e) {
      return new Set();
    }
  }

  function saveKnownRefs() {
    try {
      sessionStorage.setItem(KNOWN_REFS_KEY, JSON.stringify(Array.from(knownRefs)));
    } catch (e) {}
  }

  function loadKnownRequestIds() {
    try {
      var raw = sessionStorage.getItem(KNOWN_REQUEST_IDS_KEY);
      var parsed = raw ? JSON.parse(raw) : [];
      return new Set(Array.isArray(parsed) ? parsed.map(String) : []);
    } catch (e) {
      return new Set();
    }
  }

  function saveKnownRequestIds() {
    try {
      sessionStorage.setItem(
        KNOWN_REQUEST_IDS_KEY,
        JSON.stringify(Array.from(knownRequestIds))
      );
    } catch (e) {}
  }

  function countDispatchUnread() {
    return document.querySelectorAll("#dispatchNotificationsList .topbar-notification-item.is-unread").length;
  }

  function notificationsSupported() {
    return typeof window.Notification !== "undefined";
  }

  function permissionState() {
    if (!notificationsSupported()) return "unsupported";
    return Notification.permission || "default";
  }

  function updateDesktopNotifButton() {
    var buttons = document.querySelectorAll(".js-staff-enable-desktop-notif");
    var statuses = document.querySelectorAll(".js-staff-desktop-notif-status");
    var state = permissionState();

    function setStatuses(text, warning) {
      statuses.forEach(function (status) {
        status.hidden = !text;
        status.textContent = text || "";
        status.classList.toggle("text-warning", !!warning);
        status.classList.toggle("text-success", !!text && !warning);
        status.classList.toggle("text-muted", !!text && !warning);
      });
    }

    if (!buttons.length) return;

    if (state === "unsupported") {
      buttons.forEach(function (btn) {
        btn.hidden = true;
      });
      setStatuses("Alerts not supported on this browser or device.", true);
      return;
    }
    if (state === "granted") {
      buttons.forEach(function (btn) {
        btn.hidden = true;
      });
      setStatuses("Desktop / mobile alerts on — new goods alerts will pop up here.", false);
      return;
    }
    if (state === "denied") {
      buttons.forEach(function (btn) {
        btn.hidden = false;
        btn.innerHTML =
          '<i class="bi bi-bell-slash me-1"></i>Alerts blocked — tap for help';
      });
      setStatuses("Allow notifications for this site in browser or phone settings, then reload.", true);
      return;
    }
    buttons.forEach(function (btn) {
      btn.hidden = false;
      btn.innerHTML =
        '<i class="bi bi-bell-fill me-1"></i>Enable desktop / mobile alerts';
    });
    setStatuses("Works on desktop browsers and phones that support web notifications.", false);
  }

  function selectBestNotifTab() {
    var tabsEl = document.querySelector(".topbar-notif-tabs");
    if (!tabsEl) return;
    var driverCount = parseInt(tabsEl.getAttribute("data-driver-count") || "0", 10) || 0;
    var goodsCount = parseInt(tabsEl.getAttribute("data-goods-count") || "0", 10) || 0;
    var dispatchCount = parseInt(tabsEl.getAttribute("data-dispatch-count") || "0", 10) || 0;
    var preferred = "dispatch";
    if (driverCount > 0) preferred = "driver";
    else if (goodsCount > 0) preferred = "goods";
    else if (dispatchCount > 0) preferred = "dispatch";
    else {
      if (tabsEl.querySelector('[data-notif-tab="driver"]')) preferred = "driver";
      else if (tabsEl.querySelector('[data-notif-tab="goods"]')) preferred = "goods";
    }
    var btn = tabsEl.querySelector('[data-notif-tab="' + preferred + '"]');
    if (!btn || btn.classList.contains("active")) return;
    // Swap tab panes manually — do NOT call bootstrap.Tab.show().
    // That focuses the tab button and opens the notifications dropdown.
    var targetSel = btn.getAttribute("data-bs-target");
    var target = targetSel ? document.querySelector(targetSel) : null;
    tabsEl.querySelectorAll(".nav-link").forEach(function (el) {
      el.classList.remove("active");
      el.setAttribute("aria-selected", "false");
    });
    btn.classList.add("active");
    btn.setAttribute("aria-selected", "true");
    document.querySelectorAll(".topbar-notif-tab-content > .tab-pane").forEach(function (pane) {
      pane.classList.remove("show", "active");
    });
    if (target) {
      target.classList.add("show", "active");
    }
  }

  function ensureNotifDropdownClosed() {
    var toggle = document.querySelector(".topbar-notifications-toggle");
    if (!toggle || !window.bootstrap) return;
    try {
      var dropdown = bootstrap.Dropdown.getInstance(toggle);
      if (dropdown) dropdown.hide();
      toggle.classList.remove("show");
      toggle.setAttribute("aria-expanded", "false");
      var menu = toggle.nextElementSibling;
      if (menu && menu.classList.contains("dropdown-menu")) {
        menu.classList.remove("show");
      }
    } catch (e) {}
  }

  function syncTabCounts(goodsUnread, requestUnread) {
    var tabsEl = document.querySelector(".topbar-notif-tabs");
    if (!tabsEl) return;
    tabsEl.setAttribute("data-goods-count", String(goodsUnread || 0));
    tabsEl.setAttribute("data-driver-count", String(requestUnread || 0));
    tabsEl.setAttribute("data-dispatch-count", String(dispatchUnread || 0));
    document.querySelectorAll(".js-dispatch-notif-count").forEach(function (el) {
      el.textContent = String(dispatchUnread || 0);
    });
  }

  function requestDesktopPermission(fromUserGesture) {
    if (!notificationsSupported()) {
      return Promise.resolve("unsupported");
    }
    if (Notification.permission === "granted") {
      updateDesktopNotifButton();
      return Promise.resolve("granted");
    }
    if (Notification.permission === "denied") {
      updateDesktopNotifButton();
      return Promise.resolve("denied");
    }
    if (!fromUserGesture) {
      return Promise.resolve("default");
    }
    return Notification.requestPermission().then(function (perm) {
      try {
        sessionStorage.setItem(PERM_PROMPT_KEY, "1");
      } catch (e) {}
      updateDesktopNotifButton();
      return perm;
    });
  }

  function showDesktopNotification(item, kind) {
    if (permissionState() !== "granted") return;
    try {
      var tag =
        kind === "request"
          ? "staff-driver-request-" + (item.id || "pending")
          : "staff-farmer-goods-" + (item.batch_ref || "pending");
      var n = new Notification(item.title || "Farmer goods notification", {
        body:
          item.body ||
          (kind === "request"
            ? "Open the bell menu to issue requested goods."
            : "Open the bell menu to accept or reject."),
        icon: iconUrl,
        tag: tag,
      });
      n.onclick = function () {
        window.focus();
        if (kind === "request" && item.fulfill_url) {
          window.location.href = item.fulfill_url;
        }
        n.close();
      };
    } catch (e) {}
  }

  function notifyNewItems(items, requests) {
    if (!feedReady) {
      (items || []).forEach(function (item) {
        if (item.batch_ref) knownRefs.add(item.batch_ref);
      });
      (requests || []).forEach(function (item) {
        if (item.id != null) knownRequestIds.add(String(item.id));
      });
      saveKnownRefs();
      saveKnownRequestIds();
      feedReady = true;
      return;
    }
    var newcomers = [];
    (items || []).forEach(function (item) {
      if (!item.batch_ref) return;
      if (!knownRefs.has(item.batch_ref)) {
        newcomers.push({ kind: "issue", item: item });
        knownRefs.add(item.batch_ref);
      }
    });
    (requests || []).forEach(function (item) {
      if (item.id == null) return;
      var key = String(item.id);
      if (!knownRequestIds.has(key)) {
        newcomers.push({ kind: "request", item: item });
        knownRequestIds.add(key);
      }
    });
    saveKnownRefs();
    saveKnownRequestIds();
    if (!newcomers.length) return;
    newcomers.forEach(function (entry) {
      showDesktopNotification(entry.item, entry.kind);
    });
    if (typeof Swal !== "undefined") {
      var issueCount = newcomers.filter(function (e) {
        return e.kind === "issue";
      }).length;
      var requestCount = newcomers.filter(function (e) {
        return e.kind === "request";
      }).length;
      var titleParts = [];
      if (issueCount) {
        titleParts.push(
          issueCount === 1 ? "New farmer goods issue" : issueCount + " new farmer goods issues"
        );
      }
      if (requestCount) {
        titleParts.push(
          requestCount === 1
            ? "New driver goods request"
            : requestCount + " new driver goods requests"
        );
      }
      Swal.fire({
        toast: true,
        position: "top-end",
        icon: "info",
        title: titleParts.join(" · "),
        text: "Check the bell menu.",
        showConfirmButton: false,
        timer: 3500,
        timerProgressBar: true,
      });
    }
  }

  function updateBadge(goodsUnread, requestUnread) {
    var toggle = document.querySelector(".topbar-notifications-toggle");
    var badge = document.querySelector(".js-topbar-notif-badge");
    var count = document.querySelectorAll(".js-staff-goods-notif-count");
    var requestCount = document.querySelectorAll(".js-driver-request-notif-count");
    var goods = goodsUnread || 0;
    var requests = requestUnread || 0;
    var total = goods + requests + dispatchUnread;
    count.forEach(function (el) {
      el.textContent = String(goods);
    });
    requestCount.forEach(function (el) {
      el.textContent = String(requests);
    });
    syncTabCounts(goods, requests);
    if (total > 0) {
      if (!badge && toggle) {
        badge = document.createElement("span");
        badge.className = "topbar-notifications-badge js-topbar-notif-badge";
        toggle.appendChild(badge);
      }
      if (badge) {
        badge.hidden = false;
        badge.textContent = String(total);
      }
    } else if (badge) {
      badge.hidden = true;
      badge.textContent = "0";
    }
  }

  function renderList(items) {
    var list = document.querySelector(".js-staff-goods-notif-list");
    if (!list) return;
    itemsByRef = {};
    (items || []).forEach(function (item) {
      itemsByRef[item.batch_ref] = item;
    });
    if (!items || !items.length) {
      list.innerHTML =
        '<div class="px-3 py-3 text-muted small text-center js-staff-goods-notif-empty">No pending goods issues.</div>';
      return;
    }
    list.innerHTML = items
      .map(function (item) {
        return (
          '<div class="topbar-goods-notif-item" data-batch-ref="' +
          escapeHtml(item.batch_ref) +
          '">' +
          '<div class="topbar-goods-notif-item__title">' +
          escapeHtml(item.title) +
          "</div>" +
          '<div class="topbar-goods-notif-item__body">' +
          escapeHtml(item.body) +
          (item.created_ago ? " · " + escapeHtml(item.created_ago) : "") +
          "</div>" +
          '<div class="d-flex gap-2 flex-wrap">' +
          '<button type="button" class="btn btn-sm btn-outline-secondary js-staff-goods-review" data-batch-ref="' +
          escapeHtml(item.batch_ref) +
          '">Review</button>' +
          '<button type="button" class="btn btn-sm btn-success js-staff-goods-accept" data-url="' +
          escapeHtml(item.accept_url) +
          '" data-batch-ref="' +
          escapeHtml(item.batch_ref) +
          '">Accept</button>' +
          '<button type="button" class="btn btn-sm btn-outline-danger js-staff-goods-reject" data-url="' +
          escapeHtml(item.reject_url) +
          '" data-batch-ref="' +
          escapeHtml(item.batch_ref) +
          '">Reject</button>' +
          "</div></div>"
        );
      })
      .join("");
  }

  function renderDriverRequests(items) {
    var list = document.querySelector(".js-driver-request-notif-list");
    if (!list) return;
    if (!items || !items.length) {
      list.innerHTML =
        '<div class="px-3 py-3 text-muted small text-center js-driver-request-notif-empty">No pending driver requests.</div>';
      return;
    }
    list.innerHTML = items
      .map(function (item) {
        var actions =
          '<div class="d-flex gap-2 flex-wrap">' +
          '<a class="btn btn-sm btn-success" href="' +
          escapeHtml(item.fulfill_url) +
          '">Issue</a>';
        if (item.reject_url) {
          actions +=
            '<button type="button" class="btn btn-sm btn-outline-danger js-driver-request-reject" data-url="' +
            escapeHtml(item.reject_url) +
            '">Reject</button>';
        }
        actions += "</div>";
        return (
          '<div class="topbar-goods-notif-item" data-request-id="' +
          escapeHtml(item.id) +
          '">' +
          '<div class="topbar-goods-notif-item__title">' +
          escapeHtml(item.title) +
          "</div>" +
          '<div class="topbar-goods-notif-item__body">' +
          escapeHtml(item.body) +
          (item.created_ago ? " · " + escapeHtml(item.created_ago) : "") +
          "</div>" +
          actions +
          "</div>"
        );
      })
      .join("");
  }

  function applyFeed(data) {
    if (!data || !data.ok) return;
    dispatchUnread = countDispatchUnread();
    updateBadge(data.unread || 0, data.driver_request_unread || 0);
    renderList(data.items || []);
    renderDriverRequests(data.driver_requests || []);
    notifyNewItems(data.items || [], data.driver_requests || []);
  }

  function fetchFeed() {
    return fetch(FEED_URL, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then(function (res) {
        if (!res.ok) throw new Error("feed failed");
        return res.json();
      })
      .then(applyFeed)
      .catch(function () {});
  }

  function postAction(url, note) {
    var body = new URLSearchParams();
    body.set("note", note || "");
    return fetch(url, {
      method: "POST",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": getCsrfToken(),
        "Content-Type": "application/x-www-form-urlencoded",
      },
      credentials: "same-origin",
      body: body.toString(),
    }).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok || !data.ok) {
          throw new Error((data && data.error) || "Action failed");
        }
        return data;
      });
    });
  }

  function openModal(item) {
    var modalEl = document.getElementById("staffGoodsDetailModal");
    if (!modalEl || !window.bootstrap) return;
    modalEl.querySelector(".js-staff-goods-modal-title").textContent = item.title;
    modalEl.querySelector(".js-staff-goods-modal-body").textContent = item.body;
    modalEl.querySelector(".js-staff-goods-modal-total").textContent = item.total_amount;
    var errorEl = modalEl.querySelector(".js-staff-goods-modal-error");
    errorEl.hidden = true;
    errorEl.textContent = "";
    document.getElementById("staffGoodsNote").value = "";
    var tbody = modalEl.querySelector(".js-staff-goods-modal-lines");
    tbody.innerHTML = (item.lines || [])
      .map(function (line) {
        return (
          "<tr><td>" +
          escapeHtml(line.product_name) +
          '</td><td class="text-end">' +
          escapeHtml(line.quantity) +
          '</td><td class="text-end">' +
          escapeHtml(line.amount) +
          "</td></tr>"
        );
      })
      .join("");
    modalEl.dataset.acceptUrl = item.accept_url;
    modalEl.dataset.rejectUrl = item.reject_url;
    var receiptLink = modalEl.querySelector(".js-staff-goods-modal-receipt");
    if (receiptLink) {
      if (item.review_url) {
        receiptLink.href = item.review_url;
        receiptLink.hidden = false;
      } else {
        receiptLink.removeAttribute("href");
        receiptLink.hidden = true;
      }
    }
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
  }

  window.openStaffGoodsReviewModal = openModal;

  function setModalError(message) {
    var errorEl = document.querySelector(".js-staff-goods-modal-error");
    if (!errorEl) return;
    errorEl.hidden = !message;
    errorEl.textContent = message || "";
  }

  function toast(icon, title, text) {
    if (typeof Swal !== "undefined") {
      Swal.fire({
        toast: true,
        position: "top-end",
        icon: icon,
        title: title,
        text: text || "",
        showConfirmButton: false,
        timer: 2800,
        timerProgressBar: true,
      });
      return;
    }
    if (icon === "error" && typeof window.appAlert === "function") {
      window.appAlert(text || title, { icon: "error", title: title || "Error" });
    } else if (icon === "error") {
      window.alert(text || title);
    }
  }

  function confirmReject() {
    if (typeof window.appConfirm === "function") {
      return window.appConfirm("It will not affect stock or the payment sheet.", {
        title: "Reject this goods issue?",
        confirmText: "Reject",
        cancelText: "Cancel",
        icon: "warning",
        confirmButtonClass: "btn btn-danger",
        focusCancel: true,
      });
    }
    return Promise.resolve(
      window.confirm("Reject this goods issue? It will not affect stock or the payment sheet.")
    );
  }

  function confirmDriverRequestReject() {
    if (typeof window.appConfirm === "function") {
      return window.appConfirm("The request will be cancelled. No stock will be issued.", {
        title: "Reject this driver request?",
        confirmText: "Reject",
        cancelText: "Cancel",
        icon: "warning",
        confirmButtonClass: "btn btn-danger",
        focusCancel: true,
      });
    }
    return Promise.resolve(
      window.confirm("Reject this driver request? No stock will be issued.")
    );
  }

  function refreshAfterDriverRequestReject() {
    var path = window.location.pathname || "";
    if (path.indexOf("/farmer-goods/driver-requests/") === -1) return;
    if (/\/driver-requests\/\d+\/?$/.test(path)) {
      window.setTimeout(function () {
        window.location.href = "/suppliers/farmer-goods/driver-requests/";
      }, 350);
      return;
    }
    window.setTimeout(function () {
      window.location.reload();
    }, 350);
  }

  function confirmAccept(itemTitle) {
    var text = itemTitle
      ? itemTitle + " — stock and payment sheet will be updated."
      : "Stock and payment sheet will be updated.";
    if (typeof window.appConfirm === "function") {
      return window.appConfirm(text, {
        title: "Accept this goods issue?",
        confirmText: "Accept",
        cancelText: "Cancel",
        icon: "question",
        confirmButtonClass: "btn btn-success",
      });
    }
    return Promise.resolve(true);
  }

  function handleAction(url, fromModal, actionLabel) {
    var note = "";
    if (fromModal) {
      note = (document.getElementById("staffGoodsNote") || {}).value || "";
      setModalError("");
    }
    return postAction(url, note)
      .then(function (data) {
        applyFeed(data);
        if (fromModal) {
          var modalEl = document.getElementById("staffGoodsDetailModal");
          if (modalEl && window.bootstrap) {
            bootstrap.Modal.getOrCreateInstance(modalEl).hide();
          }
        }
        toast(
          "success",
          actionLabel === "rejected" ? "Issue rejected" : "Issue accepted",
          actionLabel === "rejected"
            ? "No stock or payment sheet change."
            : "Stock and payment sheet updated."
        );
        if (window.location.pathname.indexOf("/farmer-goods/pending-accept/") !== -1) {
          window.setTimeout(function () {
            window.location.reload();
          }, 450);
        }
      })
      .catch(function (err) {
        var message = err.message || "Action failed";
        if (fromModal) setModalError(message);
        else toast("error", "Could not complete", message);
      });
  }

  document.addEventListener("click", function (e) {
    var enableBtn = e.target.closest(".js-staff-enable-desktop-notif");
    if (enableBtn) {
      if (permissionState() === "denied") {
        if (typeof Swal !== "undefined") {
          Swal.fire({
            icon: "info",
            title: "Alerts are blocked",
            text: "Allow notifications for this site in your browser or phone settings, then reload this page.",
          });
        }
        return;
      }
      requestDesktopPermission(true).then(function (perm) {
        if (perm === "granted") {
          toast("success", "Alerts enabled", "You will get desktop / mobile popups for new goods.");
        } else if (perm === "denied" && typeof Swal !== "undefined") {
          Swal.fire({
            icon: "info",
            title: "Alerts are blocked",
            text: "Allow notifications for this site in your browser or phone settings, then reload.",
          });
        }
      });
      return;
    }
    var review = e.target.closest(".js-staff-goods-review");
    if (review) {
      var item = itemsByRef[review.getAttribute("data-batch-ref")];
      if (item) openModal(item);
      return;
    }
    var accept = e.target.closest(".js-staff-goods-accept");
    if (accept) {
      var acceptItem = itemsByRef[accept.getAttribute("data-batch-ref")];
      confirmAccept(acceptItem && acceptItem.title).then(function (ok) {
        if (ok) handleAction(accept.getAttribute("data-url"), false, "accepted");
      });
      return;
    }
    var reject = e.target.closest(".js-staff-goods-reject");
    if (reject) {
      confirmReject().then(function (ok) {
        if (ok) handleAction(reject.getAttribute("data-url"), false, "rejected");
      });
      return;
    }
    var driverReject = e.target.closest(".js-driver-request-reject");
    if (driverReject) {
      var rejectUrl = driverReject.getAttribute("data-url");
      if (!rejectUrl) return;
      confirmDriverRequestReject().then(function (ok) {
        if (!ok) return;
        var noteEl = document.getElementById("fulfill-note");
        var note = noteEl ? noteEl.value || "" : "";
        postAction(rejectUrl, note)
          .then(function (data) {
            applyFeed(data);
            toast(
              "success",
              "Request rejected",
              "No stock was issued."
            );
            refreshAfterDriverRequestReject();
          })
          .catch(function (err) {
            toast("error", "Could not reject", err.message || "Action failed");
          });
      });
      return;
    }
    if (e.target.closest(".js-staff-goods-modal-accept")) {
      var modalEl = document.getElementById("staffGoodsDetailModal");
      var modalTitle =
        modalEl && modalEl.querySelector(".js-staff-goods-modal-title")
          ? modalEl.querySelector(".js-staff-goods-modal-title").textContent
          : "";
      confirmAccept(modalTitle).then(function (ok) {
        if (ok && modalEl) handleAction(modalEl.dataset.acceptUrl, true, "accepted");
      });
      return;
    }
    if (e.target.closest(".js-staff-goods-modal-reject")) {
      confirmReject().then(function (ok) {
        var modalEl = document.getElementById("staffGoodsDetailModal");
        if (ok && modalEl) handleAction(modalEl.dataset.rejectUrl, true, "rejected");
      });
    }
  });

  document.querySelectorAll(".topbar-goods-notif-item[data-batch-ref]").forEach(function (el) {
    var ref = el.getAttribute("data-batch-ref");
    if (ref) knownRefs.add(ref);
  });
  saveKnownRefs();

  var initialEl = document.getElementById("staffGoodsInitialFeed");
  if (initialEl) {
    try {
      applyFeed(JSON.parse(initialEl.textContent));
    } catch (e) {
      feedReady = true;
    }
  } else {
    feedReady = true;
  }

  updateDesktopNotifButton();
  selectBestNotifTab();
  ensureNotifDropdownClosed();
  pollTimer = window.setInterval(fetchFeed, POLL_MS);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") fetchFeed();
  });
})();

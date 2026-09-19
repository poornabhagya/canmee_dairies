(function () {
  var FEED_URL = "/masters/driver/notifications/feed/";
  var POLL_MS = 12000;
  var KNOWN_REFS_KEY = "driverGoodsKnownBatchRefs";
  var PERM_PROMPT_KEY = "driverGoodsNotifPrompted";
  var pollTimer = null;
  var itemsByRef = {};
  var activeBatchRef = null;
  var knownRefs = loadKnownRefs();
  var feedReady = false;
  var iconUrl = "/static/pwa/app-icon.png";

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

  function notificationsSupported() {
    return typeof window.Notification !== "undefined";
  }

  function permissionState() {
    if (!notificationsSupported()) return "unsupported";
    return Notification.permission || "default";
  }

  function updateDesktopNotifButton() {
    var buttons = document.querySelectorAll(".js-driver-enable-desktop-notif");
    var statuses = document.querySelectorAll(".js-driver-desktop-notif-status");
    var state = permissionState();

    function setStatuses(text, warning) {
      statuses.forEach(function (status) {
        status.hidden = !text;
        status.textContent = text || "";
        status.classList.toggle("text-warning", !!warning);
        status.classList.toggle("text-success", !!text && !warning);
      });
    }

    if (!buttons.length) return;

    if (state === "unsupported") {
      buttons.forEach(function (btn) {
        btn.hidden = true;
      });
      setStatuses("Alerts not supported on this browser", true);
      return;
    }
    if (state === "granted") {
      buttons.forEach(function (btn) {
        btn.hidden = true;
      });
      setStatuses("Desktop / mobile alerts on", false);
      return;
    }
    if (state === "denied") {
      buttons.forEach(function (btn) {
        btn.hidden = true;
      });
      setStatuses("Alerts blocked — enable in browser settings", true);
      return;
    }
    buttons.forEach(function (btn) {
      btn.hidden = false;
    });
    setStatuses("", false);
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
      return Promise.resolve(Notification.permission);
    }
    return Notification.requestPermission().then(function (perm) {
      try {
        sessionStorage.setItem(PERM_PROMPT_KEY, "1");
      } catch (e) {}
      updateDesktopNotifButton();
      if (perm === "granted" && typeof Swal !== "undefined") {
        Swal.fire({
          toast: true,
          position: "top-end",
          icon: "success",
          title: "Desktop alerts enabled",
          showConfirmButton: false,
          timer: 2200,
        });
      }
      return perm;
    });
  }

  function vibrateBriefly() {
    try {
      if (navigator.vibrate) navigator.vibrate([120, 60, 120]);
    } catch (e) {}
  }

  function showDesktopNotification(item) {
    if (!notificationsSupported() || Notification.permission !== "granted") return;
    var title = item.title || "New farmer goods issue";
    var body = item.body || "Open the driver dashboard to accept or reject.";
    var options = {
      body: body,
      icon: iconUrl,
      badge: iconUrl,
      tag: "farmer-goods-" + (item.batch_ref || "pending"),
      renotify: true,
      requireInteraction: true,
      data: { batch_ref: item.batch_ref || "", url: window.location.href },
    };
    try {
      var note = new Notification(title, options);
      note.onclick = function () {
        window.focus();
        try {
          note.close();
        } catch (e) {}
        var toggle = document.getElementById("driverGoodsNotifToggle");
        if (toggle && window.bootstrap) {
          bootstrap.Dropdown.getOrCreateInstance(toggle).show();
        }
      };
    } catch (e) {}
    vibrateBriefly();
  }

  function notifyNewItems(items) {
    var incoming = items || [];
    var newOnes = [];
    incoming.forEach(function (item) {
      if (!item || !item.batch_ref) return;
      if (!knownRefs.has(item.batch_ref)) newOnes.push(item);
      knownRefs.add(item.batch_ref);
    });
    // Drop refs that are no longer pending
    var live = new Set(
      incoming.map(function (item) {
        return item.batch_ref;
      })
    );
    Array.from(knownRefs).forEach(function (ref) {
      if (!live.has(ref)) knownRefs.delete(ref);
    });
    saveKnownRefs();

    if (!feedReady) {
      feedReady = true;
      return;
    }
    newOnes.forEach(function (item) {
      showDesktopNotification(item);
    });
    if (newOnes.length && typeof Swal !== "undefined" && !document.hidden) {
      Swal.fire({
        toast: true,
        position: "top-end",
        icon: "info",
        title:
          newOnes.length === 1
            ? "New farmer goods issue"
            : newOnes.length + " new farmer goods issues",
        text: "Check the bell menu to accept or reject.",
        showConfirmButton: false,
        timer: 3500,
        timerProgressBar: true,
      });
    }
  }

  function updateBadge(unread) {
    var badge = document.querySelector(".js-driver-notif-badge");
    var count = document.querySelector(".js-driver-notif-count");
    if (badge) {
      if (unread > 0) {
        badge.hidden = false;
        badge.textContent = String(unread);
      } else {
        badge.hidden = true;
        badge.textContent = "0";
      }
    }
    if (count) count.textContent = String(unread || 0);
  }

  function renderList(items) {
    var list = document.querySelector(".js-driver-notif-list");
    if (!list) return;
    itemsByRef = {};
    (items || []).forEach(function (item) {
      itemsByRef[item.batch_ref] = item;
    });
    if (!items || !items.length) {
      list.innerHTML =
        '<div class="px-3 py-4 text-muted small text-center js-driver-notif-empty">No pending goods issues.</div>';
      return;
    }
    list.innerHTML = items
      .map(function (item) {
        return (
          '<div class="driver-notif-item" data-batch-ref="' +
          escapeHtml(item.batch_ref) +
          '">' +
          '<div class="driver-notif-item__title">' +
          escapeHtml(item.title) +
          "</div>" +
          '<div class="driver-notif-item__body">' +
          escapeHtml(item.body) +
          (item.created_ago ? " · " + escapeHtml(item.created_ago) : "") +
          "</div>" +
          '<div class="d-flex gap-2 flex-wrap">' +
          '<button type="button" class="btn btn-sm btn-outline-secondary js-driver-goods-review" data-batch-ref="' +
          escapeHtml(item.batch_ref) +
          '">Review</button>' +
          '<button type="button" class="btn btn-sm btn-success js-driver-goods-accept" data-url="' +
          escapeHtml(item.accept_url) +
          '" data-batch-ref="' +
          escapeHtml(item.batch_ref) +
          '">Accept</button>' +
          '<button type="button" class="btn btn-sm btn-outline-danger js-driver-goods-reject" data-url="' +
          escapeHtml(item.reject_url) +
          '" data-batch-ref="' +
          escapeHtml(item.batch_ref) +
          '">Reject</button>' +
          "</div></div>"
        );
      })
      .join("");
  }

  function applyFeed(data) {
    if (!data || !data.ok) return;
    updateBadge(data.unread || 0);
    renderList(data.items || []);
    notifyNewItems(data.items || []);
    var panelCount = document.querySelector(".js-driver-pending-panel-count");
    if (panelCount) panelCount.textContent = String(data.unread || 0);
    var panel = document.querySelector(".js-driver-pending-panel");
    if (panel) {
      panel.hidden = !(data.unread > 0);
    }
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
    activeBatchRef = item.batch_ref;
    var modalEl = document.getElementById("driverGoodsDetailModal");
    if (!modalEl || !window.bootstrap) return;
    modalEl.querySelector(".js-driver-goods-modal-title").textContent = item.title;
    modalEl.querySelector(".js-driver-goods-modal-body").textContent = item.body;
    modalEl.querySelector(".js-driver-goods-modal-total").textContent = item.total_amount;
    var errorEl = modalEl.querySelector(".js-driver-goods-modal-error");
    errorEl.hidden = true;
    errorEl.textContent = "";
    document.getElementById("driverGoodsNote").value = "";
    var tbody = modalEl.querySelector(".js-driver-goods-modal-lines");
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
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
  }

  function setModalError(message) {
    var errorEl = document.querySelector(".js-driver-goods-modal-error");
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
      note = (document.getElementById("driverGoodsNote") || {}).value || "";
      setModalError("");
    }
    return postAction(url, note)
      .then(function (data) {
        applyFeed(data);
        if (fromModal) {
          var modalEl = document.getElementById("driverGoodsDetailModal");
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
      })
      .catch(function (err) {
        var message = err.message || "Action failed";
        if (fromModal) setModalError(message);
        else toast("error", "Could not complete", message);
      });
  }

  document.addEventListener("click", function (e) {
    var enableBtn = e.target.closest(".js-driver-enable-desktop-notif");
    if (enableBtn) {
      requestDesktopPermission(true).then(function (perm) {
        if (perm === "denied" && typeof Swal !== "undefined") {
          Swal.fire({
            icon: "info",
            title: "Alerts are blocked",
            text: "Allow notifications for this site in your browser or phone settings, then reload.",
          });
        }
      });
      return;
    }
    var review = e.target.closest(".js-driver-goods-review");
    if (review) {
      var item = itemsByRef[review.getAttribute("data-batch-ref")];
      if (item) openModal(item);
      return;
    }
    var accept = e.target.closest(".js-driver-goods-accept");
    if (accept) {
      var acceptItem = itemsByRef[accept.getAttribute("data-batch-ref")];
      confirmAccept(acceptItem && acceptItem.title).then(function (ok) {
        if (ok) handleAction(accept.getAttribute("data-url"), false, "accepted");
      });
      return;
    }
    var reject = e.target.closest(".js-driver-goods-reject");
    if (reject) {
      confirmReject().then(function (ok) {
        if (ok) handleAction(reject.getAttribute("data-url"), false, "rejected");
      });
      return;
    }
    if (e.target.closest(".js-driver-goods-modal-accept")) {
      var modalEl = document.getElementById("driverGoodsDetailModal");
      var modalTitle =
        modalEl && modalEl.querySelector(".js-driver-goods-modal-title")
          ? modalEl.querySelector(".js-driver-goods-modal-title").textContent
          : "";
      confirmAccept(modalTitle).then(function (ok) {
        if (ok) handleAction(modalEl && modalEl.dataset.acceptUrl, true, "accepted");
      });
      return;
    }
    if (e.target.closest(".js-driver-goods-modal-reject")) {
      confirmReject().then(function (ok) {
        if (!ok) return;
        var modalReject = document.getElementById("driverGoodsDetailModal");
        handleAction(modalReject && modalReject.dataset.rejectUrl, true, "rejected");
      });
    }
  });

  // Seed from server-rendered items if present
  document.querySelectorAll(".driver-notif-item[data-batch-ref]").forEach(function (el) {
    var ref = el.getAttribute("data-batch-ref");
    if (ref) {
      itemsByRef[ref] = itemsByRef[ref] || { batch_ref: ref };
      knownRefs.add(ref);
    }
  });
  saveKnownRefs();

  updateDesktopNotifButton();
  fetchFeed();
  pollTimer = window.setInterval(fetchFeed, POLL_MS);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) fetchFeed();
  });
})();

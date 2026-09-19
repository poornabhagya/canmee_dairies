(function () {
  "use strict";

  function getCsrfToken() {
    var name = "csrftoken=";
    var parts = document.cookie.split(";");
    for (var i = 0; i < parts.length; i++) {
      var c = parts[i].replace(/^\s+/, "");
      if (c.indexOf(name) === 0) {
        return decodeURIComponent(c.substring(name.length));
      }
    }
    var input = document.querySelector("#user-roles-form [name=csrfmiddlewaretoken]");
    return input ? input.value : "";
  }

  function rolesUrl(userId) {
    var template = window.USER_ROLES_UPDATE_URL_TEMPLATE || "";
    return template.replace("/0/", "/" + userId + "/");
  }

  function statusUrl(userId) {
    var template = window.USER_STATUS_UPDATE_URL_TEMPLATE || "";
    return template.replace("/0/", "/" + userId + "/");
  }

  function parseRoleIds(raw) {
    if (!raw) return [];
    return String(raw)
      .split(",")
      .map(function (v) {
        return v.trim();
      })
      .filter(Boolean);
  }

  function renderRoleBadges(container, roles, userId, username) {
    if (!container) return;
    container.innerHTML = "";
    var title = (roles || []).map(function (r) { return r.name; }).join(", ");
    if (title) container.setAttribute("title", title);
    else container.removeAttribute("title");

    var canChange = !!document.getElementById("userRolesModal");
    var roleIds = (roles || []).map(function (r) { return String(r.id); }).join(",");

    if (!roles || !roles.length) {
      var empty = document.createElement("span");
      empty.className = "text-muted small user-role-empty";
      empty.textContent = "None";
      container.appendChild(empty);
      return;
    }

    if (canChange && userId) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "badge text-bg-light border text-dark user-role-badge user-count-badge text-truncate js-user-roles-btn";
      btn.setAttribute("data-user-id", userId);
      btn.setAttribute("data-username", username || "user");
      btn.setAttribute("data-role-ids", roleIds);
      btn.title = title + " — click to manage roles";
      btn.textContent = roles.length === 1 ? roles[0].name : roles.length + " roles";
      container.appendChild(btn);
      return;
    }

    if (roles.length === 1) {
      var single = document.createElement("span");
      single.className = "badge text-bg-light border text-dark user-role-badge text-truncate";
      single.textContent = roles[0].name;
      container.appendChild(single);
      return;
    }
    var count = document.createElement("button");
    count.type = "button";
    count.className = "badge text-bg-light border text-dark user-role-badge user-count-badge";
    count.setAttribute("data-bs-toggle", "popover");
    count.setAttribute("data-bs-trigger", "focus");
    count.setAttribute("data-bs-placement", "top");
    count.setAttribute("data-bs-content", title);
    count.setAttribute("title", "Assigned roles");
    count.textContent = roles.length + " roles";
    container.appendChild(count);
    if (window.bootstrap) bootstrap.Popover.getOrCreateInstance(count);
  }

  function syncRowRoleButtons(userId, roleIds) {
    var joined = roleIds.join(",");
    document.querySelectorAll('.js-user-roles-btn[data-user-id="' + userId + '"]').forEach(function (btn) {
      btn.setAttribute("data-role-ids", joined);
    });
  }

  function initUserStatusSelects() {
    document.addEventListener("change", function (e) {
      var select = e.target.closest(".js-user-status-select");
      if (!select) return;

      var userId = select.getAttribute("data-user-id");
      var username = select.getAttribute("data-username") || "user";
      var newValue = select.value;
      var previousValue = select.getAttribute("data-prev-value") || newValue;
      if (newValue === previousValue) return;

      function saveStatus() {
        select.disabled = true;
        var body = new FormData();
        body.append("is_active", newValue);

        fetch(statusUrl(userId), {
          method: "POST",
          headers: {
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": getCsrfToken(),
          },
          body: body,
          credentials: "same-origin",
        })
          .then(function (res) {
            return res.json().then(function (data) {
              return { ok: res.ok, data: data };
            });
          })
          .then(function (result) {
            if (!result.ok || !result.data.ok) {
              select.value = previousValue;
              var msg = (result.data && result.data.error) || "Could not update status.";
              if (typeof window.appAlert === "function") {
                window.appAlert(msg, { icon: "error", title: "Update failed" });
              } else if (window.Swal) {
                Swal.fire({ icon: "error", title: "Update failed", text: msg });
              } else {
                alert(msg);
              }
              return;
            }
            select.setAttribute("data-prev-value", newValue);
            select.classList.toggle("is-inactive", newValue === "0");
            var row = select.closest("tr");
            if (row) row.classList.toggle("user-row--inactive", newValue === "0");
            if (window.Swal) {
              Swal.fire({
                icon: "success",
                title: username + " is now " + (newValue === "1" ? "active" : "inactive"),
                timer: 1600,
                showConfirmButton: false,
                toast: true,
                position: "top-end",
              });
            }
          })
          .catch(function () {
            select.value = previousValue;
            if (typeof window.appAlert === "function") {
              window.appAlert("Network error. Please try again.", {
                icon: "error",
                title: "Update failed",
              });
            } else if (window.Swal) {
              Swal.fire({
                icon: "error",
                title: "Update failed",
                text: "Network error. Please try again.",
              });
            } else {
              alert("Network error. Please try again.");
            }
          })
          .finally(function () {
            select.disabled = false;
          });
      }

      if (newValue === "0") {
        if (typeof window.appConfirm === "function") {
          window
            .appConfirm("They will no longer be able to sign in.", {
              title: "Deactivate " + username + "?",
              confirmText: "Deactivate",
              cancelText: "Cancel",
              icon: "warning",
            })
            .then(function (ok) {
              if (ok) {
                saveStatus();
              } else {
                select.value = previousValue;
              }
            });
        } else if (window.Swal) {
          Swal.fire({
            icon: "warning",
            title: "Deactivate " + username + "?",
            text: "They will no longer be able to sign in.",
            showCancelButton: true,
            confirmButtonText: "Deactivate",
            cancelButtonText: "Cancel",
          }).then(function (result) {
            if (result.isConfirmed) {
              saveStatus();
            } else {
              select.value = previousValue;
            }
          });
        } else if (confirm("Deactivate " + username + "?")) {
          saveStatus();
        } else {
          select.value = previousValue;
        }
        return;
      }

      saveStatus();
    });

    document.querySelectorAll(".js-user-status-select").forEach(function (select) {
      select.setAttribute("data-prev-value", select.value);
    });
  }

  function initUserRolesModal() {
    var modalEl = document.getElementById("userRolesModal");
    var form = document.getElementById("user-roles-form");
    if (!modalEl || !form || !window.bootstrap) return;

    var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    var usernameEl = document.getElementById("user-roles-modal-username");
    var saveBtn = document.getElementById("user-roles-save-btn");
    var currentUserId = null;

    function openForUser(btn) {
      currentUserId = btn.getAttribute("data-user-id");
      var username = btn.getAttribute("data-username") || "user";
      var roleIds = parseRoleIds(btn.getAttribute("data-role-ids"));
      if (usernameEl) usernameEl.textContent = username;
      form.action = rolesUrl(currentUserId);
      form.querySelectorAll(".js-user-role-checkbox").forEach(function (cb) {
        cb.checked = roleIds.indexOf(cb.value) !== -1;
      });
      modal.show();
    }

    document.addEventListener("click", function (e) {
      var btn = e.target.closest(".js-user-roles-btn");
      if (!btn) return;
      e.preventDefault();
      openForUser(btn);
    });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (!currentUserId) return;

      if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving…';
      }

      var body = new FormData(form);
      fetch(form.action, {
        method: "POST",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": getCsrfToken(),
        },
        body: body,
        credentials: "same-origin",
      })
        .then(function (res) {
          return res.json().then(function (data) {
            return { ok: res.ok, data: data };
          });
        })
        .then(function (result) {
          if (!result.ok || !result.data.ok) {
            var msg = (result.data && result.data.error) || "Could not save roles.";
            if (typeof window.appAlert === "function") {
              window.appAlert(msg, { icon: "error", title: "Save failed" });
            } else if (window.Swal) {
              Swal.fire({ icon: "error", title: "Save failed", text: msg });
            } else {
              alert(msg);
            }
            return;
          }
          var roles = result.data.roles || [];
          var roleIds = roles.map(function (r) {
            return String(r.id);
          });
          var badgeContainer = document.querySelector(
            '.user-role-badges[data-user-id="' + currentUserId + '"]'
          );
          var username =
            (usernameEl && usernameEl.textContent) ||
            (document.querySelector(
              '.js-user-roles-btn[data-user-id="' + currentUserId + '"]'
            ) &&
              document
                .querySelector('.js-user-roles-btn[data-user-id="' + currentUserId + '"]')
                .getAttribute("data-username")) ||
            "user";
          renderRoleBadges(badgeContainer, roles, currentUserId, username);
          syncRowRoleButtons(currentUserId, roleIds);
          modal.hide();
          if (window.Swal) {
            Swal.fire({
              icon: "success",
              title: "Roles saved",
              timer: 1800,
              showConfirmButton: false,
              toast: true,
              position: "top-end",
            });
          }
        })
        .catch(function () {
          if (typeof window.appAlert === "function") {
            window.appAlert("Network error. Please try again.", {
              icon: "error",
              title: "Save failed",
            });
          } else if (window.Swal) {
            Swal.fire({
              icon: "error",
              title: "Save failed",
              text: "Network error. Please try again.",
            });
          } else {
            alert("Network error. Please try again.");
          }
        })
        .finally(function () {
          if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<i class="bi bi-check2 me-1"></i>Save roles';
          }
        });
    });
  }

  function initUserActionDropdowns() {
    if (!window.bootstrap) return;
    document.querySelectorAll(".user-actions-dropdown [data-bs-toggle='dropdown']").forEach(function (toggle) {
      bootstrap.Dropdown.getOrCreateInstance(toggle, {
        popperConfig: function (defaultConfig) {
          var config = defaultConfig || {};
          config.strategy = "fixed";
          config.placement = "left-start";
          config.modifiers = (config.modifiers || []).concat([
            {
              name: "preventOverflow",
              options: { boundary: document.body, padding: 8 },
            },
          ]);
          return config;
        },
      });
    });
  }

  function initPopovers() {
    if (!window.bootstrap) return;
    document.querySelectorAll('[data-bs-toggle="popover"]').forEach(function (el) {
      bootstrap.Popover.getOrCreateInstance(el);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initUserRolesModal();
    initUserStatusSelects();
    initUserActionDropdowns();
    initPopovers();
  });
})();

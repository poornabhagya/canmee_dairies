(function () {
  "use strict";

  function capitalizeFirst(text) {
    var s = String(text || "").trim();
    if (!s) return "";
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function syncRoleOptionStyles() {
    document.querySelectorAll(".assign-role-option").forEach(function (label) {
      var input = label.querySelector(".js-assign-role-checkbox");
      if (!input) return;
      label.classList.toggle("is-checked", input.checked);
    });
  }

  function setRoleCheckboxes(roleIds) {
    var idSet = {};
    (roleIds || []).forEach(function (id) {
      idSet[String(id)] = true;
    });
    document.querySelectorAll(".js-assign-role-checkbox").forEach(function (cb) {
      cb.checked = !!idSet[cb.value];
    });
    syncRoleOptionStyles();
  }

  function renderCurrentRoles(roles) {
    var container = document.getElementById("assign-roles-current-roles");
    if (!container) return;
    container.innerHTML = "";
    if (!roles || !roles.length) {
      var empty = document.createElement("span");
      empty.className = "text-muted small";
      empty.textContent = "No roles assigned yet.";
      container.appendChild(empty);
      return;
    }
    roles.forEach(function (role) {
      var badge = document.createElement("span");
      badge.className = "badge text-bg-light border text-dark";
      badge.textContent = role.name;
      container.appendChild(badge);
    });
  }

  function renderUserPanel(user) {
    var panel = document.getElementById("assign-roles-user-panel");
    var nameEl = document.getElementById("assign-roles-user-name");
    var metaEl = document.getElementById("assign-roles-user-meta");
    if (!panel || !nameEl || !metaEl) return;

    if (!user) {
      panel.classList.remove("is-visible");
      nameEl.textContent = "—";
      metaEl.textContent = "";
      renderCurrentRoles([]);
      return;
    }

    panel.classList.add("is-visible");
    nameEl.textContent = user.display_name || capitalizeFirst(user.username);
    var metaParts = ["@" + capitalizeFirst(user.username)];
    if (user.email) metaParts.push(user.email);
    metaParts.push(user.is_active ? "Active" : "Inactive");
    if (user.is_staff) metaParts.push("Staff");
    if (user.branches && user.branches.length) {
      metaParts.push(user.branches.join(", "));
    }
    metaEl.textContent = metaParts.join(" · ");
    renderCurrentRoles(user.roles || []);
  }

  function loadUserRoles(userId) {
    if (!userId) {
      renderUserPanel(null);
      setRoleCheckboxes([]);
      return Promise.resolve();
    }

    var url = window.ASSIGN_ROLES_USER_DATA_URL + "?user=" + encodeURIComponent(userId);
    return fetch(url, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok || !result.data.ok) {
          renderUserPanel(null);
          return;
        }
        var user = result.data.user;
        renderUserPanel(user);
        setRoleCheckboxes(user.role_ids || []);
      })
      .catch(function () {
        renderUserPanel(null);
      });
  }

  function initAssignRolesPage() {
    var userSelect = document.getElementById("assign-roles-user");
    if (!userSelect) return;

    if (window.jQuery && window.jQuery.fn.select2) {
      window.jQuery(userSelect).select2({
        width: "100%",
        placeholder: "Search and select a user…",
        allowClear: true,
      });
      window.jQuery(userSelect).on("change select2:select select2:unselect", function () {
        loadUserRoles(userSelect.value);
      });
    } else {
      userSelect.addEventListener("change", function () {
        loadUserRoles(userSelect.value);
      });
    }

    document.querySelectorAll(".js-assign-role-checkbox").forEach(function (cb) {
      cb.addEventListener("change", syncRoleOptionStyles);
    });

    document.querySelector(".js-assign-roles-select-all") &&
      document.querySelector(".js-assign-roles-select-all").addEventListener("click", function (e) {
        e.preventDefault();
        document.querySelectorAll(".js-assign-role-checkbox").forEach(function (cb) {
          cb.checked = true;
        });
        syncRoleOptionStyles();
      });

    document.querySelector(".js-assign-roles-clear-all") &&
      document.querySelector(".js-assign-roles-clear-all").addEventListener("click", function (e) {
        e.preventDefault();
        document.querySelectorAll(".js-assign-role-checkbox").forEach(function (cb) {
          cb.checked = false;
        });
        syncRoleOptionStyles();
      });

    var preselected = window.ASSIGN_ROLES_PRESELECTED_USER || userSelect.value;
    if (preselected) {
      loadUserRoles(preselected);
    } else {
      syncRoleOptionStyles();
    }
  }

  document.addEventListener("DOMContentLoaded", initAssignRolesPage);
})();

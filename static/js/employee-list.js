(function () {
  "use strict";

  function getCsrfToken() {
    if (window.EMPLOYEE_CSRF_TOKEN) return window.EMPLOYEE_CSRF_TOKEN;
    var input = document.querySelector("[name=csrfmiddlewaretoken]");
    return input ? input.value : "";
  }

  function applyStatusSelectStyle(select) {
    select.classList.remove(
      "is-inactive",
      "is-resigned",
      "is-terminated",
      "employee-status-select--active",
      "employee-status-select--inactive",
      "employee-status-select--resigned",
      "employee-status-select--terminated"
    );
    var val = select.value;
    select.classList.add("employee-status-select--" + val);
    if (val === "inactive" || val === "resigned") select.classList.add("is-inactive");
    if (val === "terminated") select.classList.add("is-terminated");
  }

  function applyRowStyle(row, status) {
    if (!row) return;
    row.className = row.className
      .replace(/employee-row--\w+/g, "")
      .replace(/smart-row--\w+/g, "")
      .trim();
    row.classList.add("employee-row--" + status);
    row.classList.add("smart-row--" + status);
  }

  function initEmployeeResignModal() {
    var modalEl = document.getElementById("employeeResignModal");
    var form = document.getElementById("employee-resign-form");
    if (!modalEl || !form || !window.bootstrap) return;

    var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    var nameEl = document.getElementById("employee-resign-name");

    function openResign(btn) {
      if (nameEl) nameEl.textContent = btn.getAttribute("data-employee-name") || "employee";
      form.action = btn.getAttribute("data-resign-url") || "";
      modal.show();
    }

    document.addEventListener("click", function (e) {
      var btn = e.target.closest(".js-employee-resign-btn");
      if (!btn) return;
      e.preventDefault();
      openResign(btn);
    });

    return { openResign: openResign };
  }

  function initEmployeeTerminateModal() {
    var modalEl = document.getElementById("employeeTerminateModal");
    var form = document.getElementById("employee-terminate-form");
    if (!modalEl || !form || !window.bootstrap) return;

    var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    var nameEl = document.getElementById("employee-terminate-name");

    function openTerminate(btn) {
      if (nameEl) nameEl.textContent = btn.getAttribute("data-employee-name") || "employee";
      form.action = btn.getAttribute("data-terminate-url") || "";
      modal.show();
    }

    document.addEventListener("click", function (e) {
      var btn = e.target.closest(".js-employee-terminate-btn");
      if (!btn) return;
      e.preventDefault();
      openTerminate(btn);
    });

    return { openTerminate: openTerminate };
  }

  function initEmployeeStatusSelects(resignModal, terminateModal) {
    document.addEventListener("change", function (e) {
      var select = e.target.closest(".js-employee-status-select");
      if (!select) return;

      var newValue = select.value;
      var previousValue = select.getAttribute("data-prev-value") || newValue;
      if (newValue === previousValue) return;

      if (newValue === "resigned") {
        select.value = previousValue;
        if (resignModal && resignModal.openResign) {
          resignModal.openResign(select);
        }
        return;
      }

      if (newValue === "terminated") {
        select.value = previousValue;
        if (terminateModal && terminateModal.openTerminate) {
          terminateModal.openTerminate(select);
        }
        return;
      }

      select.disabled = true;
      var body = new FormData();
      body.append("status", newValue);

      fetch(select.getAttribute("data-status-url"), {
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
            applyStatusSelectStyle(select);
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
          applyStatusSelectStyle(select);
          applyRowStyle(select.closest("tr"), newValue);
          if (window.Swal) {
            Swal.fire({
              icon: "success",
              title: "Status updated",
              timer: 1400,
              showConfirmButton: false,
              toast: true,
              position: "top-end",
            });
          }
        })
        .catch(function () {
          select.value = previousValue;
          applyStatusSelectStyle(select);
          if (typeof window.appAlert === "function") {
            window.appAlert("Network error.", { icon: "error", title: "Update failed" });
          } else if (window.Swal) {
            Swal.fire({ icon: "error", title: "Update failed", text: "Network error." });
          } else {
            alert("Network error.");
          }
        })
        .finally(function () {
          select.disabled = false;
        });
    });

    document.querySelectorAll(".js-employee-status-select").forEach(function (select) {
      select.setAttribute("data-prev-value", select.value);
      applyStatusSelectStyle(select);
    });
  }

  function initEmployeeActionDropdowns() {
    if (!window.bootstrap) return;
    document.querySelectorAll(".employee-actions-dropdown [data-bs-toggle='dropdown']").forEach(function (toggle) {
      bootstrap.Dropdown.getOrCreateInstance(toggle, {
        popperConfig: function (defaultConfig) {
          var config = defaultConfig || {};
          config.strategy = "fixed";
          config.placement = "left-start";
          return config;
        },
      });
    });
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function displayValue(value) {
    var text = String(value == null ? "" : value).trim();
    return text ? escapeHtml(text) : "—";
  }

  function rowHtml(label, value) {
    return "<div><dt>" + escapeHtml(label) + "</dt><dd>" + displayValue(value) + "</dd></div>";
  }

  function sectionHtml(title, rows, wide) {
    return (
      '<section class="employee-view-section' +
      (wide ? " employee-view-section--wide" : "") +
      '"><h3>' +
      escapeHtml(title) +
      '</h3><dl class="employee-view-dl">' +
      rows.join("") +
      "</dl></section>"
    );
  }

  function buildEmployeeViewHtml(emp) {
    var identity = [
      rowHtml("Employee #", emp.employee_number),
      rowHtml("Common name", emp.common_name),
      rowHtml("Full name", emp.full_name),
      rowHtml("Initial", emp.initial),
      rowHtml("NIC", emp.nic),
      rowHtml("Date of birth", emp.date_of_birth),
    ];
    var contact = [
      rowHtml("Email", emp.email),
      rowHtml("Mobile", emp.phone),
      rowHtml("WhatsApp", emp.whatsapp),
    ];
    var employment = [
      rowHtml("Job title", emp.job_title),
      rowHtml("Department", emp.department),
      rowHtml("Branches", (emp.branches || []).join(", ")),
      rowHtml("Hire date", emp.hire_date),
      rowHtml("Status", emp.status_display),
      rowHtml(
        "Attendance login",
        emp.nic
          ? emp.attendance_password_set
            ? "Password set"
            : "Pending first login"
          : "NIC not set"
      ),
    ];
    var account = emp.username
      ? [
          rowHtml("Username", emp.username),
          rowHtml("Account email", emp.user_email),
          rowHtml("Account status", emp.user_active ? "Active" : "Inactive"),
          rowHtml("Roles", (emp.user_roles || []).join(", ")),
          rowHtml("Last login", emp.last_login),
        ]
      : [rowHtml("Linked account", "No user account linked")];
    var extras = [
      rowHtml("Documents", emp.documents_count),
      rowHtml("Notes", emp.notes),
      rowHtml("Created", emp.date_created),
      rowHtml("Last updated", emp.last_updated),
    ];
    var html =
      '<div class="employee-view-grid">' +
      sectionHtml("Identity", identity) +
      sectionHtml("Contact", contact) +
      sectionHtml("Employment", employment) +
      sectionHtml("User account", account);
    if (emp.status === "resigned" || emp.resignation_date || emp.resignation_reason) {
      html += sectionHtml("Resignation", [
        rowHtml("Date", emp.resignation_date),
        rowHtml("Reason", emp.resignation_reason),
        rowHtml("Notes", emp.resignation_notes),
        rowHtml("Recorded by", emp.resigned_by),
      ]);
    }
    if (emp.status === "terminated" || emp.termination_date || emp.termination_reason) {
      html += sectionHtml("Termination", [
        rowHtml("Date", emp.termination_date),
        rowHtml("Reason", emp.termination_reason),
        rowHtml("Notes", emp.termination_notes),
        rowHtml("Recorded by", emp.terminated_by),
      ]);
    }
    html += sectionHtml("Other", extras, true) + "</div>";
    return html;
  }

  function showEmployeeView(emp) {
    if (!window.Swal) {
      window.location.href = emp.detail_url;
      return;
    }
    Swal.fire({
      title: emp.full_name || "Employee details",
      html: buildEmployeeViewHtml(emp),
      width: "52rem",
      customClass: {
        popup: "employee-view-swal",
        confirmButton: "btn btn-outline-secondary",
        denyButton: "btn btn-primary",
        actions: "gap-2",
      },
      buttonsStyling: false,
      showConfirmButton: true,
      confirmButtonText: "Close",
      showDenyButton: !!emp.detail_url,
      denyButtonText: "Open full page",
      focusConfirm: true,
    }).then(function (result) {
      if (result.isDenied && emp.detail_url) {
        window.location.href = emp.detail_url;
      }
    });
  }

  function initEmployeeView() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest(".js-employee-view");
      if (!btn) return;
      e.preventDefault();
      var url = btn.getAttribute("data-json-url");
      if (!url) return;
      btn.disabled = true;
      fetch(url, {
        headers: { "X-Requested-With": "XMLHttpRequest", Accept: "application/json" },
        credentials: "same-origin",
      })
        .then(function (res) {
          return res.json().then(function (data) {
            return { ok: res.ok, data: data };
          });
        })
        .then(function (result) {
          if (!result.ok || !result.data.ok || !result.data.employee) {
            var msg =
              (result.data && result.data.error) || "Could not load employee details.";
            if (typeof window.appAlert === "function") {
              window.appAlert(msg, { icon: "error", title: "View failed" });
            } else if (window.Swal) {
              Swal.fire({ icon: "error", title: "View failed", text: msg });
            } else {
              alert(msg);
            }
            return;
          }
          showEmployeeView(result.data.employee);
        })
        .catch(function () {
          if (typeof window.appAlert === "function") {
            window.appAlert("Network error.", { icon: "error", title: "View failed" });
          } else if (window.Swal) {
            Swal.fire({ icon: "error", title: "View failed", text: "Network error." });
          } else {
            alert("Network error.");
          }
        })
        .finally(function () {
          btn.disabled = false;
        });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var resignModal = initEmployeeResignModal();
    var terminateModal = initEmployeeTerminateModal();
    initEmployeeStatusSelects(resignModal, terminateModal);
    initEmployeeActionDropdowns();
    initEmployeeView();
  });
})();

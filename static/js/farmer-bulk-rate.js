(function () {
  "use strict";

  var config = window.FARMER_BULK_RATE || {};
  var saveUrl = config.saveUrl || "";
  var inFlight = Object.create(null);
  var enterNavigation = false;

  function getCsrfToken() {
    if (config.csrfToken) return config.csrfToken;
    var input = document.querySelector("[name=csrfmiddlewaretoken]");
    return input ? input.value : "";
  }

  function isVisibleRow(row) {
    return !!(row && row.offsetParent !== null);
  }

  function getRateInputs(visibleOnly) {
    var inputs = Array.prototype.slice.call(document.querySelectorAll(".js-bulk-rate-input"));
    if (!visibleOnly) return inputs;
    return inputs.filter(function (input) {
      return isVisibleRow(getRow(input));
    });
  }

  function getRow(el) {
    return el ? el.closest(".js-bulk-rate-row") : null;
  }

  function getRateInput(row) {
    return row ? row.querySelector(".js-bulk-rate-input") : null;
  }

  function getApplySelect(row) {
    return row ? row.querySelector(".js-bulk-rate-apply") : null;
  }

  function getLatestCell(row) {
    return row ? row.querySelector(".js-bulk-rate-latest") : null;
  }

  function rowApplyValue(row) {
    var applySelect = getApplySelect(row);
    if (applySelect) return applySelect.value;
    return row.getAttribute("data-apply-rate-paid") || "yes";
  }

  function rowPayload(row) {
    var rateInput = getRateInput(row);
    if (!rateInput) return null;
    var farmerId = row.getAttribute("data-farmer-id");
    if (!farmerId) return null;
    return {
      farmer_id: farmerId,
      rate: rateInput.value.trim(),
      apply_rate_paid: rowApplyValue(row),
    };
  }

  function isRowDirty(row) {
    var rateInput = getRateInput(row);
    if (!rateInput) return false;
    var rateDirty =
      rateInput.value.trim() !== (rateInput.getAttribute("data-original-rate") || "");
    var applySelect = getApplySelect(row);
    if (!applySelect) return rateDirty;
    return (
      rateDirty ||
      applySelect.value !== (applySelect.getAttribute("data-original-apply") || "")
    );
  }

  function markRowSaved(row, data) {
    var rateInput = getRateInput(row);
    var applySelect = getApplySelect(row);
    var latestCell = getLatestCell(row);

    if (rateInput && data.rate !== undefined) {
      rateInput.value = data.rate;
      rateInput.setAttribute("data-original-rate", data.rate);
    }
    if (applySelect && data.apply_rate_paid !== undefined) {
      applySelect.value = data.apply_rate_paid;
      applySelect.setAttribute("data-original-apply", data.apply_rate_paid);
    }
    if (latestCell && data.rate_display) {
      latestCell.textContent = data.rate_display;
    }
  }

  function flashSaved(row, input) {
    if (row) {
      row.classList.add("bulk-rate-row--saved");
      window.setTimeout(function () {
        row.classList.remove("bulk-rate-row--saved");
      }, 900);
    }
    if (input) {
      input.classList.remove("bulk-rate-input--error");
      input.classList.add("bulk-rate-input--saved");
      window.setTimeout(function () {
        input.classList.remove("bulk-rate-input--saved");
      }, 900);
    }
  }

  function markError(input, message) {
    if (input) {
      input.classList.remove("bulk-rate-input--saved");
      input.classList.add("bulk-rate-input--error");
    }
    if (window.Swal) {
      Swal.fire({
        icon: "error",
        title: "Save failed",
        text: message || "Could not save rate.",
        timer: 2200,
        showConfirmButton: false,
        toast: true,
        position: "top-end",
      });
    }
  }

  function saveRow(row, options) {
    options = options || {};
    if (!row || !saveUrl) return Promise.resolve({ ok: false });

    var payload = rowPayload(row);
    if (!payload || !payload.farmer_id) return Promise.resolve({ ok: false });

    if (!options.force && !isRowDirty(row)) {
      return Promise.resolve({ ok: true, skipped: true });
    }

    var farmerId = payload.farmer_id;
    if (inFlight[farmerId]) return inFlight[farmerId];

    var rateInput = getRateInput(row);
    var applySelect = getApplySelect(row);
    if (rateInput) rateInput.disabled = true;
    if (applySelect) applySelect.disabled = true;

    var promise = fetch(saveUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-CSRFToken": getCsrfToken(),
        "X-Requested-With": "XMLHttpRequest",
      },
      body: new URLSearchParams(payload).toString(),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok || !result.data.ok) {
          var msg = (result.data && result.data.error) || "Could not save rate.";
          markError(rateInput, msg);
          return { ok: false, error: msg };
        }
        markRowSaved(row, result.data);
        if (result.data.saved) {
          flashSaved(row, rateInput);
        }
        return { ok: true, data: result.data };
      })
      .catch(function () {
        markError(rateInput, "Network error. Please try again.");
        return { ok: false, error: "Network error." };
      })
      .finally(function () {
        delete inFlight[farmerId];
        if (rateInput) rateInput.disabled = false;
        if (applySelect) applySelect.disabled = false;
      });

    inFlight[farmerId] = promise;
    return promise;
  }

  function focusNextRateInput(currentInput) {
    var inputs = getRateInputs(true);
    var index = inputs.indexOf(currentInput);
    if (index === -1) return;
    var next = inputs[index + 1];
    if (next) {
      enterNavigation = true;
      next.focus();
      next.select();
      window.setTimeout(function () {
        enterNavigation = false;
      }, 0);
    }
  }

  function initDelegatedEvents() {
    var root = document.getElementById("bulk-rate-form") || document;
    if (!root) return;

    root.addEventListener("keydown", function (e) {
      var input = e.target && e.target.classList && e.target.classList.contains("js-bulk-rate-input")
        ? e.target
        : null;
      if (!input || e.key !== "Enter") return;
      e.preventDefault();
      var row = getRow(input);
      saveRow(row).then(function (result) {
        if (result.ok) {
          focusNextRateInput(input);
        }
      });
    });

    root.addEventListener("focusin", function (e) {
      var input = e.target && e.target.classList && e.target.classList.contains("js-bulk-rate-input")
        ? e.target
        : null;
      if (!input) return;
      window.setTimeout(function () {
        input.select();
      }, 0);
    });

    root.addEventListener("focusout", function (e) {
      var input = e.target && e.target.classList && e.target.classList.contains("js-bulk-rate-input")
        ? e.target
        : null;
      if (!input || enterNavigation) return;
      var row = getRow(input);
      if (isRowDirty(row)) {
        saveRow(row);
      }
    });

    root.addEventListener("change", function (e) {
      var select = e.target && e.target.classList && e.target.classList.contains("js-bulk-rate-apply")
        ? e.target
        : null;
      if (!select) return;
      saveRow(getRow(select));
    });
  }

  initDelegatedEvents();
})();

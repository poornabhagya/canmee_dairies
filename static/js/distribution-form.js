(function () {
  "use strict";

  function initDistributionForm(form) {
  if (!form) return;

  var configEl = form.querySelector("#distribution-form-config") || document.getElementById("distribution-form-config");
  var config = {};
  if (configEl) {
    try {
      config = JSON.parse(configEl.textContent || "{}");
    } catch (e) {
      config = {};
    }
  }

  var buyerBranchOptionsEl = form.querySelector("#buyer-branch-options") || document.getElementById("buyer-branch-options");
  var buyerBranchOptions = config.buyerBranchOptions || [];
  if (buyerBranchOptionsEl) {
    try {
      buyerBranchOptions = JSON.parse(buyerBranchOptionsEl.textContent || "[]") || [];
    } catch (e) {
      buyerBranchOptions = [];
    }
  }

  function listFocusables() {
    var sel =
      'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="reset"]),' +
      "select, textarea";
    var nodes = form.querySelectorAll(sel);
    var out = [];
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.disabled || el.readOnly) continue;
      if (el.type === "radio" || el.type === "checkbox") continue;
      if (el.closest("[hidden]")) continue;
      if (el.closest(".d-none")) continue;
      out.push(el);
    }
    return out;
  }

  var buyerWrap = form.querySelector("#buyer-field-wrap");
  var branchWrap = form.querySelector("#destination-branch-wrap");
  var destLabel = form.querySelector("#dispatch-destination-label");
  var dispatchToInputs = form.querySelectorAll('input[name="dispatch_to"]');
  var buyerSelect = form.querySelector("#id_buyer");
  var sourceBranchSelect = form.querySelector("#id_branch");
  var destBranchSelect = form.querySelector("#id_destination_branch");
  var kgInput = form.querySelector("#id_kg");
  var dispatchNoInput = form.querySelector("#id_dispatch_no");
  var fatInput = form.querySelector("#id_fat");
  var lrInput = form.querySelector("#id_lr");
  var snfInput = form.querySelector("#id_snf");
  var alcoholResultSelect = form.querySelector("#id_alcohol_result");
  var dateInput = form.querySelector("#id_date");

  function currentDispatchTo() {
    var selected = "buyer";
    dispatchToInputs.forEach(function (el) {
      if (el.checked) selected = el.value;
    });
    return selected;
  }

  function setMultiBranchValues(selectEl, branchIds) {
    if (!selectEl) return;
    var ids = (branchIds || []).map(String);
    Array.prototype.forEach.call(selectEl.options, function (opt) {
      opt.selected = ids.indexOf(String(opt.value)) >= 0;
    });
    if (window.jQuery) {
      var $el = window.jQuery(selectEl);
      if ($el.hasClass("select2-hidden-accessible")) {
        $el.val(ids).trigger("change");
      }
    }
  }

  function branchListIncludes(branchList, branchId) {
    if (!branchId || isNaN(branchId)) return true;
    return (branchList || []).some(function (id) {
      return Number(id) === Number(branchId);
    });
  }

  function filterBuyerOptions() {
    if (!buyerSelect) return;
    var branchId = sourceBranchSelect ? parseInt(sourceBranchSelect.value, 10) : NaN;
    var current = buyerSelect.value;
    buyerSelect.innerHTML = "";
    var placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Select buyer";
    buyerSelect.appendChild(placeholder);
    buyerBranchOptions.forEach(function (buyer) {
      if (branchListIncludes(buyer.branches, branchId)) {
        buyerSelect.appendChild(
          new Option(buyer.name, buyer.id, false, String(buyer.id) === current)
        );
      }
    });
    buyerSelect.value =
      current && buyerSelect.querySelector('option[value="' + current + '"]') ? current : "";
    if (window.jQuery) {
      var $buyer = window.jQuery(buyerSelect);
      if ($buyer.hasClass("select2-hidden-accessible")) {
        $buyer.trigger("change.select2");
      }
    }
  }

  if (sourceBranchSelect) {
    if (window.jQuery) {
      window.jQuery(sourceBranchSelect).on("change select2:select", filterBuyerOptions);
    } else {
      sourceBranchSelect.addEventListener("change", filterBuyerOptions);
    }
  }
  filterBuyerOptions();
  window.setTimeout(filterBuyerOptions, 0);

  function focusNextFrom(fromEl) {
    if (!fromEl) return;
    var focusables = listFocusables();
    var idx = focusables.indexOf(fromEl);
    var next = idx === -1 ? null : focusables[idx + 1];
    if (!next) return;
    window.setTimeout(function () {
      if (window.jQuery && jQuery(next).hasClass("select2-hidden-accessible")) {
        jQuery(next).select2("open");
        return;
      }
      next.focus();
      if (next.tagName === "SELECT" || next.tagName === "TEXTAREA") return;
      if (typeof next.select === "function" && next.type !== "email" && next.type !== "date" && next.type !== "time") {
        try {
          next.select();
        } catch (err) {}
      }
    }, 0);
  }

  function syncDispatchDestination() {
    var toBranch = currentDispatchTo() === "branch";
    if (buyerWrap) buyerWrap.classList.toggle("d-none", toBranch);
    if (branchWrap) branchWrap.classList.toggle("d-none", !toBranch);
    if (destLabel) {
      destLabel.setAttribute("for", toBranch ? "id_destination_branch" : "id_buyer");
    }
  }

  form.querySelectorAll(".dispatch-segment__option").forEach(function (opt) {
    opt.addEventListener("click", function (e) {
      if (form.getAttribute("data-view-only") === "1") return;
      var input = opt.querySelector('input[name="dispatch_to"]');
      if (!input || input.disabled) return;
      if (input.checked && e.target !== input) return;
      input.checked = true;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });

  dispatchToInputs.forEach(function (el) {
    el.addEventListener("change", function () {
      syncDispatchDestination();
      if (form.getAttribute("data-view-only") === "1") return;
      if (currentDispatchTo() === "branch" && destBranchSelect) {
        if (window.jQuery && jQuery(destBranchSelect).hasClass("select2-hidden-accessible")) {
          jQuery(destBranchSelect).select2("open");
        } else {
          destBranchSelect.focus();
        }
      } else if (buyerSelect) {
        if (window.jQuery && jQuery(buyerSelect).hasClass("select2-hidden-accessible")) {
          jQuery(buyerSelect).select2("open");
        } else {
          buyerSelect.focus();
        }
      }
    });
  });
  syncDispatchDestination();

  function focusQuantity() {
    if (!kgInput) return;
    kgInput.focus();
    if (typeof kgInput.select === "function") {
      try {
        kgInput.select();
      } catch (err) {}
    }
  }

  var skipAdvance = true;
  window.setTimeout(function () { skipAdvance = false; }, 250);

  function advanceOnValue(el) {
    if (!el) return;
    function go() {
      if (skipAdvance) return;
      if (form.getAttribute("data-view-only") === "1") return;
      if (!String(el.value || "").trim()) return;
      focusNextFrom(el);
    }
    el.addEventListener("change", go);
    if (window.jQuery) {
      window.jQuery(el).on("select2:select", go);
    }
  }
  advanceOnValue(buyerSelect);
  advanceOnValue(destBranchSelect);
  advanceOnValue(alcoholResultSelect);
  if (dateInput) {
    dateInput.addEventListener("change", function () {
      if (skipAdvance) return;
      focusNextFrom(dateInput);
    });
  }

  var KG_TO_L = Number(config.kgToLiters || "0");
  var qtyUnit = "liters";
  var qtyUnitBtns = form.querySelectorAll(".qty-segment__btn[data-qty-unit]");
  var qtyLabel = form.querySelector("#dispatch-qty-label");

  function formatQty2(n) {
    return (Math.round(n * 100) / 100).toFixed(2);
  }

  function parseQtyVal() {
    if (!kgInput) return NaN;
    var n = parseFloat(String(kgInput.value || "").replace(",", "."));
    return isNaN(n) ? NaN : n;
  }

  function applyQtyChrome(unit) {
    qtyUnit = unit;
    qtyUnitBtns.forEach(function (btn) {
      var u = btn.getAttribute("data-qty-unit");
      var on = u === unit;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
    if (qtyLabel) {
      qtyLabel.textContent = unit === "liters" ? "Quantity (liters)" : "Quantity (kg)";
    }
  }

  function switchQtyUnit(next) {
    if (!kgInput || next === qtyUnit) return;
    var n = parseQtyVal();
    if (!isNaN(n)) {
      if (qtyUnit === "kg" && next === "liters") {
        kgInput.value = formatQty2(n * KG_TO_L);
      } else if (qtyUnit === "liters" && next === "kg") {
        kgInput.value = formatQty2(n / KG_TO_L);
      }
    }
    applyQtyChrome(next);
  }

  function syncQtyFieldToKgForSubmit() {
    if (!kgInput) return;
    var unitField = form.querySelector("#id_quantity_unit");
    if (unitField) unitField.value = qtyUnit;
    var n = parseQtyVal();
    if (isNaN(n)) return;
    kgInput.value = qtyUnit === "liters" ? formatQty2(n / KG_TO_L) : formatQty2(n);
  }

  qtyUnitBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (form.getAttribute("data-view-only") === "1") return;
      switchQtyUnit(btn.getAttribute("data-qty-unit"));
      focusQuantity();
    });
  });

  var unitField = form.querySelector("#id_quantity_unit");
  var initialUnit = unitField && unitField.value === "kg" ? "kg" : "liters";
  applyQtyChrome(initialUnit);
  if (kgInput && kgInput.value) {
    var storedKg = parseQtyVal();
    if (!isNaN(storedKg) && initialUnit === "liters") {
      kgInput.value = formatQty2(storedKg * KG_TO_L);
    }
  }

  function parseDecimal(el) {
    if (!el) return NaN;
    var s = String(el.value || "").trim().replace(",", ".");
    if (!s) return NaN;
    var n = parseFloat(s);
    return isNaN(n) ? NaN : n;
  }

  function autoComputeSnf() {
    if (!snfInput) return;
    var fat = parseDecimal(fatInput);
    var lr = parseDecimal(lrInput);
    if (isNaN(fat) || isNaN(lr)) {
      snfInput.value = "";
      autoComputeTs();
      return;
    }
    var snf = lr / 4 + 0.2 * fat + 0.36;
    snfInput.value = formatQty2(snf);
    autoComputeTs();
  }

  var tsInput = form.querySelector("#id_ts");
  function autoComputeTs() {
    if (!tsInput) return;
    var fat = parseDecimal(fatInput);
    var snf = parseDecimal(snfInput);
    if (isNaN(fat) || isNaN(snf)) {
      tsInput.value = "";
      return;
    }
    tsInput.value = formatQty2(fat + snf);
  }

  var rateUrl = form.getAttribute("data-buyer-rate-url") || "";
  var manualRateCheck = document.getElementById("id_is_rate_manual");
  var unitRateInput = document.getElementById("id_unit_rate");
  var rateUnitSelect = document.getElementById("id_rate_unit");
  var rateHint = document.getElementById("dispatch-rate-hint");
  var rateSection = document.getElementById("dispatch-rate-section");
  var rateFetchTimer;

  function isBuyerDispatch() {
    return currentDispatchTo() === "buyer";
  }

  function setRateFieldsEnabled(manual) {
    if (!unitRateInput || !rateUnitSelect) return;
    if (unitRateInput.disabled) return;
    unitRateInput.readOnly = !manual;
    rateUnitSelect.disabled = !manual;
  }

  function applyPeriodRate(data) {
    if (!unitRateInput || !rateUnitSelect || !data) return;
    if (manualRateCheck && manualRateCheck.checked) return;
    unitRateInput.value = data.rate || "0.00";
    if (data.rate_unit) rateUnitSelect.value = data.rate_unit;
    if (rateHint) {
      rateHint.textContent = data.rate_unit_label
        ? "Period rate: " + data.rate + " per " + data.rate_unit_label.replace(/^Per /i, "").toLowerCase()
        : "Period rate applied automatically.";
    }
  }

  function loadPeriodRate() {
    if (!rateUrl || !buyerSelect || !isBuyerDispatch()) {
      if (rateSection) rateSection.classList.toggle("d-none", !isBuyerDispatch());
      return;
    }
    if (rateSection) rateSection.classList.remove("d-none");
    var buyerId = buyerSelect.value;
    var dateVal = dateInput ? dateInput.value : "";
    if (!buyerId) return;
    clearTimeout(rateFetchTimer);
    rateFetchTimer = setTimeout(function () {
      var url = rateUrl + "?buyer=" + encodeURIComponent(buyerId) + "&date=" + encodeURIComponent(dateVal);
      fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" }, credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data && data.ok) applyPeriodRate(data);
        })
        .catch(function () {});
    }, 150);
  }

  if (manualRateCheck) {
    setRateFieldsEnabled(manualRateCheck.checked);
    manualRateCheck.addEventListener("change", function () {
      setRateFieldsEnabled(manualRateCheck.checked);
      if (rateHint) {
        rateHint.textContent = manualRateCheck.checked
          ? "This dispatch uses the rate you enter."
          : "Period rate applies automatically.";
      }
      if (!manualRateCheck.checked) loadPeriodRate();
    });
  }
  if (buyerSelect) buyerSelect.addEventListener("change", loadPeriodRate);
  if (dateInput) {
    dateInput.addEventListener("change", loadPeriodRate);
    dateInput.addEventListener("blur", loadPeriodRate);
  }
  dispatchToInputs.forEach(function (el) {
    el.addEventListener("change", loadPeriodRate);
  });
  loadPeriodRate();
  autoComputeTs();

  if (fatInput) {
    fatInput.addEventListener("input", autoComputeSnf);
    fatInput.addEventListener("blur", autoComputeSnf);
  }
  if (lrInput) {
    lrInput.addEventListener("input", autoComputeSnf);
    lrInput.addEventListener("blur", autoComputeSnf);
  }
  autoComputeSnf();

  form.addEventListener("submit", function () {
    syncQtyFieldToKgForSubmit();
    autoComputeSnf();
  }, true);

  form.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || e.isComposing) return;
    var target = e.target;
    if (!form.contains(target)) return;
    if (form.getAttribute("data-view-only") === "1") {
      e.preventDefault();
      return;
    }
    if (target.closest(".select2-container")) return;
    if (target.tagName === "BUTTON" || target.tagName === "A") return;
    if (target.tagName === "TEXTAREA") return;

    if (dateInput && (target === dateInput || target.classList.contains("js-datepicker"))) {
      e.preventDefault();
      if (target._flatpickr) {
        if (target._flatpickr.isOpen) target._flatpickr.close();
        else target._flatpickr.open();
      } else {
        focusNextFrom(target);
      }
      return;
    }

    e.preventDefault();

    function focusNext(el) {
      if (!el) return;
      if (window.jQuery && jQuery(el).hasClass("select2-hidden-accessible")) {
        jQuery(el).select2("open");
        return;
      }
      el.focus();
      if (el.tagName === "SELECT" || el.tagName === "TEXTAREA") return;
      if (typeof el.select === "function" && el.type !== "email" && el.type !== "date" && el.type !== "time") {
        try {
          el.select();
        } catch (err) {}
      }
    }

    if (kgInput && target === kgInput && dispatchNoInput) {
      focusNext(dispatchNoInput);
      return;
    }

    if (lrInput && target === lrInput) {
      autoComputeSnf();
      focusNext(alcoholResultSelect);
      return;
    }

    var focusables = listFocusables();
    var idx = focusables.indexOf(target);
    if (idx === -1) return;
    focusNext(focusables[idx + 1]);
  });

  var buyerQuickAddForm = document.getElementById("dispatch-buyer-quick-add-form");
  var buyerQuickAddModalEl = document.getElementById("dispatchBuyerQuickAddModal");

  function clearDispatchBuyerErrors() {
    if (!buyerQuickAddForm) return;
    buyerQuickAddForm.querySelectorAll(".is-invalid").forEach(function (el) {
      el.classList.remove("is-invalid");
    });
    document.querySelectorAll("[data-dispatch-buyer-error]").forEach(function (el) {
      el.textContent = "";
    });
  }

  function setDispatchBuyerErrors(errors) {
    if (!buyerQuickAddForm) return;
    Object.keys(errors || {}).forEach(function (field) {
      var input = buyerQuickAddForm.querySelector('[name="' + field + '"]');
      var msg = document.querySelector('[data-dispatch-buyer-error="' + field + '"]');
      if (input) input.classList.add("is-invalid");
      if (msg) msg.textContent = (errors[field] || []).join(" ");
    });
  }

  if (buyerQuickAddForm && buyerSelect) {
    buyerQuickAddForm.addEventListener("submit", function (e) {
      e.preventDefault();
      clearDispatchBuyerErrors();
      var submitBtn = buyerQuickAddForm.querySelector('button[type="submit"]');
      if (submitBtn) submitBtn.disabled = true;
      var fd = new FormData(buyerQuickAddForm);
      var branchId = sourceBranchSelect ? sourceBranchSelect.value : "";
      if (branchId && !fd.getAll("branches").length) {
        fd.append("branches", branchId);
      }
      fetch(buyerQuickAddForm.action, {
        method: "POST",
        body: fd,
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then(function (r) {
          return r.json().then(function (data) {
            return { ok: r.ok, data: data };
          });
        })
        .then(function (res) {
          if (!res.ok || !res.data || !res.data.ok) {
            setDispatchBuyerErrors((res.data && res.data.errors) || {});
            if (window.Swal && !(res.data && res.data.errors)) {
              Swal.fire({ icon: "error", title: "Could not create buyer." });
            }
            return;
          }
          var buyer = res.data.buyer || {};
          var buyerId = String(buyer.id || "");
          var buyerName = buyer.name || buyerQuickAddForm.querySelector('[name="name"]').value || "";
          var branchIdNum = sourceBranchSelect ? parseInt(sourceBranchSelect.value, 10) : NaN;
          if (buyerId) {
            buyerBranchOptions.push({
              id: Number(buyer.id),
              name: buyerName,
              branches: branchIdNum && !isNaN(branchIdNum) ? [branchIdNum] : [],
            });
            filterBuyerOptions();
            buyerSelect.value = buyerId;
            buyerSelect.dispatchEvent(new Event("change", { bubbles: true }));
          }
          if (buyerQuickAddModalEl && window.bootstrap) {
            bootstrap.Modal.getOrCreateInstance(buyerQuickAddModalEl).hide();
          }
          focusQuantity();
        })
        .catch(function () {
          if (window.Swal) {
            Swal.fire({ icon: "error", title: "Could not create buyer." });
          }
        })
        .finally(function () {
          if (submitBtn) submitBtn.disabled = false;
        });
    });
  }

  if (buyerQuickAddModalEl && buyerQuickAddForm) {
    buyerQuickAddModalEl.addEventListener("hidden.bs.modal", function () {
      buyerQuickAddForm.reset();
      setMultiBranchValues(buyerQuickAddForm.querySelector('[name="branches"]'), []);
      clearDispatchBuyerErrors();
    });
    buyerQuickAddModalEl.addEventListener("shown.bs.modal", function () {
      var branchId = sourceBranchSelect ? sourceBranchSelect.value : "";
      if (branchId) {
        setMultiBranchValues(buyerQuickAddForm.querySelector('[name="branches"]'), [branchId]);
      }
      var nameInput = buyerQuickAddForm.querySelector('[name="name"]');
      if (nameInput) nameInput.focus();
    });
  }
  }

  window.initDistributionForm = initDistributionForm;
  document.querySelectorAll("form.js-distribution-form").forEach(function (el) {
    if (el.closest("#dispatchEditModal")) return;
    initDistributionForm(el);
  });
})();

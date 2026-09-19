/**
 * Flatpickr: single dates + range filter (syncs hidden start/end).
 */
(function () {
  "use strict";

  if (typeof flatpickr === "undefined") {
    return;
  }

  var prevArrow =
    '<span class="fp-nav-btn" aria-hidden="true"><svg width="10" height="16" viewBox="0 0 10 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M8 1L2 8l6 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></span>';
  var nextArrow =
    '<span class="fp-nav-btn" aria-hidden="true"><svg width="10" height="16" viewBox="0 0 10 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M2 1l6 7-6 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></span>';

  function appendClear(instance, onClear) {
    var footer = document.createElement("div");
    footer.className = "flatpickr-clear-footer";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "flatpickr-clear-btn";
    btn.textContent = "Clear";
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      onClear();
    });
    footer.appendChild(btn);
    instance.calendarContainer.appendChild(footer);
  }

  function singlePickerOptions(overrides) {
    var options = {
      dateFormat: "Y-m-d",
      allowInput: false,
      disableMobile: true,
      prevArrow: prevArrow,
      nextArrow: nextArrow,
      onReady: function (selectedDates, dateStr, instance) {
        appendClear(instance, function () {
          instance.clear();
        });
      },
    };
    if (overrides) {
      Object.keys(overrides).forEach(function (key) {
        if (key === "onReady" && typeof overrides.onReady === "function") {
          var baseReady = options.onReady;
          options.onReady = function () {
            baseReady.apply(this, arguments);
            overrides.onReady.apply(this, arguments);
          };
        } else {
          options[key] = overrides[key];
        }
      });
    }
    return options;
  }

  window.CanmeeDatepicker = {
    initSingle: function (el, overrides) {
      if (!el || el._flatpickr) return el._flatpickr;
      return flatpickr(el, singlePickerOptions(overrides));
    },
    destroy: function (el) {
      if (el && el._flatpickr) {
        el._flatpickr.destroy();
      }
    },
  };

  function initSingleInputs() {
    document.querySelectorAll("input.js-datepicker:not(.flatpickr-input)").forEach(function (el) {
      if (el.classList.contains("js-datepicker-range")) return;
      var inModal = el.closest(".modal") || el.classList.contains("js-datepicker-in-modal");
      window.CanmeeDatepicker.initSingle(
        el,
        inModal ? { appendTo: document.body, static: false } : null
      );
    });
  }

  function initDatepickerTriggers() {
    document.querySelectorAll(".js-datepicker-trigger").forEach(function (btn) {
      if (btn.dataset.fpBound === "1") return;
      btn.dataset.fpBound = "1";
      function openTarget() {
        var sel = btn.getAttribute("data-target") || "";
        var input = sel ? document.querySelector(sel) : null;
        if (!input && btn.parentElement) {
          input = btn.parentElement.querySelector("input.js-datepicker");
        }
        if (!input) return;
        if (!input._flatpickr && window.CanmeeDatepicker) {
          window.CanmeeDatepicker.initSingle(input);
        }
        if (input._flatpickr) input._flatpickr.open();
      }
      btn.addEventListener("click", openTarget);
      btn.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openTarget();
        }
      });
    });
  }

  function initRangeInputs() {
    document.querySelectorAll("input.js-datepicker-range:not(.flatpickr-input)").forEach(function (el) {
      var form = el.closest("form");
      if (!form) return;
      var startName = el.getAttribute("data-range-start") || "start";
      var endName = el.getAttribute("data-range-end") || "end";
      var startEl = form.querySelector('input[name="' + startName + '"]');
      var endEl = form.querySelector('input[name="' + endName + '"]');
      if (!startEl || !endEl) return;

      var defaultDate = [];
      if (startEl.value) defaultDate.push(startEl.value);
      if (endEl.value) defaultDate.push(endEl.value);

      var useIsoDisplay = el.classList.contains("js-datepicker-range-iso");
      var fpOptions = {
        mode: "range",
        dateFormat: "Y-m-d",
        allowInput: false,
        disableMobile: true,
        prevArrow: prevArrow,
        nextArrow: nextArrow,
        defaultDate: defaultDate.length ? defaultDate : undefined,
        onChange: function (selectedDates, dateStr, fpInstance) {
          if (selectedDates.length === 2) {
            startEl.value = fpInstance.formatDate(selectedDates[0], "Y-m-d");
            endEl.value = fpInstance.formatDate(selectedDates[1], "Y-m-d");
          } else if (selectedDates.length === 1) {
            var day = fpInstance.formatDate(selectedDates[0], "Y-m-d");
            startEl.value = day;
            endEl.value = day;
          } else if (selectedDates.length === 0) {
            startEl.value = "";
            endEl.value = "";
          }
        },
        onReady: function (selectedDates, dateStr, fpInstance) {
          appendClear(fpInstance, function () {
            fpInstance.clear();
            startEl.value = "";
            endEl.value = "";
          });
          var wrap = el.closest(".flatpickr-range-wrap");
          if (wrap) {
            var trigger = wrap.querySelector(".input-group-text");
            if (trigger) {
              trigger.addEventListener("click", function () {
                fpInstance.open();
              });
            }
            if (!useIsoDisplay) {
              var altInput = wrap.querySelector(".fp-range-alt-input");
              if (altInput) {
                altInput.addEventListener("click", function () {
                  fpInstance.open();
                });
              }
            } else if (el) {
              el.addEventListener("click", function () {
                fpInstance.open();
              });
            }
          }
        },
      };

      if (!useIsoDisplay) {
        fpOptions.altInput = true;
        fpOptions.altFormat = el.getAttribute("data-alt-format") || "M j, Y";
        fpOptions.altInputClass = "form-control border-start-0 fp-range-alt-input";
      }

      var instance = flatpickr(el, fpOptions);
      el._flatpickrInstance = instance;
    });
  }

  function initAll() {
    initRangeInputs();
    initSingleInputs();
    initDatepickerTriggers();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
})();

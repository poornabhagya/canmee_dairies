(function ($) {
  "use strict";
  if (!$) return;

  function updateCount($sel, $countEl) {
    var selected = ($sel.val() || []).length;
    var total = $sel.find("option").length;
    var text;
    if (selected === 0) {
      text = "None selected";
    } else if (selected === total && total > 0) {
      text = "All " + total + " selected";
    } else {
      text = selected + " of " + total + " selected";
    }
    $countEl.text(text);
  }

  function applyCompactChoices($sel, maxVisible) {
    var $container = $sel.next(".select2-container");
    if (!$container.length) return;

    var vals = $sel.val() || [];
    var $rendered = $container.find(".select2-selection__rendered");
    var $choices = $rendered
      .find(".select2-selection__choice")
      .not(".branch-multi-select__summary");
    $rendered.find(".branch-multi-select__summary").remove();

    if (vals.length > maxVisible) {
      $choices.hide();
      $(
        '<li class="select2-selection__choice branch-multi-select__summary" role="presentation">' +
          '<span class="select2-selection__choice__display">' +
          vals.length +
          " branches selected</span></li>"
      ).prependTo($rendered);
    } else {
      $choices.show();
    }
  }

  function initBranchMultiSelect($wrap) {
    var $sel = $wrap.find("select.js-branch-multi-select");
    if (!$sel.length) return;

    var $count = $wrap.find(".branch-multi-select__count");
    var maxVisible = parseInt($wrap.data("max-visible-chips"), 10);
    if (isNaN(maxVisible)) maxVisible = 2;

    function refresh() {
      updateCount($sel, $count);
      applyCompactChoices($sel, maxVisible);
    }

    $sel.on("change select2:select select2:unselect", refresh);
    refresh();

    $wrap.on("click", ".js-branch-select-all", function (e) {
      e.preventDefault();
      var all = $sel
        .find("option")
        .map(function () {
          return this.value;
        })
        .get();
      $sel.val(all).trigger("change");
    });

    $wrap.on("click", ".js-branch-clear-all", function (e) {
      e.preventDefault();
      $sel.val(null).trigger("change");
    });
  }

  window.initBranchMultiSelects = function (root) {
    var $root = root ? $(root) : $(document);
    $root.find(".branch-multi-select").each(function () {
      initBranchMultiSelect($(this));
    });
  };

  $(function () {
    window.initBranchMultiSelects();
  });
})(window.jQuery);

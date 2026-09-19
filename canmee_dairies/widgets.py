from django import forms


class FlatpickrDateInput(forms.DateInput):
    """Text input + Flatpickr (see static/js/datepicker.js)."""

    input_type = "text"

    def __init__(self, attrs=None, format=None):
        attrs = attrs.copy() if attrs else {}
        attrs.setdefault("class", "form-control js-datepicker")
        attrs.setdefault("placeholder", "Select date")
        attrs.setdefault("autocomplete", "off")
        fmt = format or "%Y-%m-%d"
        super().__init__(attrs, fmt)

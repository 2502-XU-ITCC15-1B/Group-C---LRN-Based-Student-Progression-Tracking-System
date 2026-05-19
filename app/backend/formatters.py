def status_badge_class(status):
    classes = {
        "ENROLLED": "bg-success",
        "TRANSFER_IN": "bg-info text-dark",
        "PENDING_TRANSFER_IN": "bg-warning text-dark",
        "TRANSFER_OUT": "bg-danger",
        "MISSING": "bg-secondary",
        "REPEATED": "bg-warning text-dark",
        "COMPLETED": "bg-primary",
        "DELAYED_COMPLETED": "bg-info text-dark",
        "STRAIGHT_PATH": "bg-success",
        "INCOMPLETE": "bg-secondary",
    }
    return classes.get(status, "bg-dark")


def humanize_status(status):
    labels = {
        "ENROLLED": "Enrolled",
        "TRANSFER_IN": "Transferred In",
        "PENDING_TRANSFER_IN": "Pending Transfer-In",
        "TRANSFER_OUT": "Transferred Out",
        "MISSING": "Missing",
        "REPEATED": "Repeater",
        "COMPLETED": "Completed",
        "DELAYED_COMPLETED": "Completed - Irregular",
        "STRAIGHT_PATH": "Regular",
        "INCOMPLETE": "Incomplete",
    }
    return labels.get(status, str(status or "").replace("_", " ").title())


def normalize_gender(value):
    gender = str(value).strip().upper()

    if gender.startswith("M"):
        return "MALE"
    if gender.startswith("F"):
        return "FEMALE"
    return "UNKNOWN"


def normalize_remarks(value):
    remarks = str(value).strip()

    if remarks.lower() == "nan":
        return ""

    return remarks


def detect_status_from_remarks(remarks):
    normalized = str(remarks).upper().replace("\n", " ").strip()

    if not normalized or normalized == "NAN":
        return "ENROLLED"

    transfer_out_patterns = [
        "T/O",
        "T-O",
        "TRANSFER OUT",
        "TRANSFERRED OUT",
        "TRANSFER-OUT",
    ]

    transfer_in_patterns = [
        "T/I",
        "T-I",
        "TRANSFER IN",
        "TRANSFERRED IN",
        "TRANSFER-IN",
    ]

    if "PENDING TI" in normalized or "PENDING T/I" in normalized:
        return "PENDING_TRANSFER_IN"

    if any(pattern in normalized for pattern in transfer_out_patterns):
        return "TRANSFER_OUT"

    if any(pattern in normalized for pattern in transfer_in_patterns):
        return "TRANSFER_IN"

    return "ENROLLED"


def format_remarks(status, remarks):
    clean_remarks = normalize_remarks(remarks)
    status_label = humanize_status(status)

    abbreviated_remarks = {
        "T/I": "Transferred In",
        "T-I": "Transferred In",
        "TI": "Transferred In",
        "TRANSFER IN": "Transferred In",
        "PENDING T/I": "Pending Transfer-In",
        "PENDING TI": "Pending Transfer-In",
        "T/O": "Transferred Out",
        "T-O": "Transferred Out",
        "TO": "Transferred Out",
        "TRANSFER OUT": "Transferred Out",
    }

    if clean_remarks.upper() in abbreviated_remarks:
        return abbreviated_remarks[clean_remarks.upper()]

    if status in {"TRANSFER_IN", "PENDING_TRANSFER_IN", "TRANSFER_OUT"} and not clean_remarks:
        return status_label

    return clean_remarks

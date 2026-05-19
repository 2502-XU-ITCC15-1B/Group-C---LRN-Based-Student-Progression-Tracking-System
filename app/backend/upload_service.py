import os
import re
import secrets
import tempfile
import time

import pandas as pd
from flask import session

from .constants import LRN_PATTERN, PENDING_UPLOAD_TTL_SECONDS, SUPPORTED_UPLOAD_EXTENSIONS
from .validators import is_valid_school_year


def read_lis_upload(file):
    filename = (file.filename or "").strip().lower()
    extension = os.path.splitext(filename)[1]

    if extension not in SUPPORTED_UPLOAD_EXTENSIONS:
        raise ValueError("Unsupported file type. Upload a LIS/SF1 file in .xlsx, .xls, or .csv format.")

    if extension == ".csv":
        try:
            return pd.read_csv(file, header=None, dtype=str, encoding="utf-8-sig")
        except UnicodeDecodeError:
            file.stream.seek(0)
            return pd.read_csv(file, header=None, dtype=str, encoding="latin1")

    return pd.read_excel(file, header=None)


def detect_upload_metadata(df):
    detected = {"school_year": None, "grade_level": None}

    for _, row in df.head(15).iterrows():
        for value in row:
            text = str(value).upper().replace("\n", " ").strip()
            if not text or text == "NAN":
                continue

            if detected["school_year"] is None:
                year_match = re.search(r"\b(\d{4})\s*[-–—]\s*(\d{4})\b", text)
                if year_match:
                    school_year = f"{year_match.group(1)}-{year_match.group(2)}"
                    if is_valid_school_year(school_year):
                        detected["school_year"] = school_year

            if detected["grade_level"] is None:
                grade_match = re.search(r"\b(?:GRADE|GRADE LEVEL|GR\.?)\s*[:\-]?\s*(7|8|9|10)\b", text)
                if grade_match:
                    detected["grade_level"] = int(grade_match.group(1))

            if detected["school_year"] and detected["grade_level"]:
                return detected

    return detected


def get_upload_metadata_mismatches(detected, school_year, grade_level):
    mismatches = []

    if detected.get("school_year") and detected["school_year"] != school_year:
        mismatches.append({
            "field": "School Year",
            "detected": detected["school_year"],
            "selected": school_year,
        })

    if detected.get("grade_level") and detected["grade_level"] != grade_level:
        mismatches.append({
            "field": "Grade Level",
            "detected": f"Grade {detected['grade_level']}",
            "selected": f"Grade {grade_level}",
        })

    return mismatches


def get_pending_upload_dir():
    path = os.path.join(tempfile.gettempdir(), "lrn_upload_confirmations")
    os.makedirs(path, exist_ok=True)
    return path


def save_pending_upload(file):
    extension = os.path.splitext(file.filename or "")[1].lower()
    token = secrets.token_urlsafe(24)
    path = os.path.join(get_pending_upload_dir(), f"{token}{extension}")

    file.stream.seek(0)
    with open(path, "wb") as output:
        output.write(file.stream.read())

    pending_uploads = session.get("pending_uploads", {})
    pending_uploads[token] = {
        "path": path,
        "filename": os.path.basename(file.filename or f"upload{extension}"),
        "created_at": time.time(),
    }
    session["pending_uploads"] = pending_uploads
    return token


def pop_pending_upload(token):
    pending_uploads = session.get("pending_uploads", {})
    pending = pending_uploads.pop(token, None)
    session["pending_uploads"] = pending_uploads

    if not pending:
        return None

    is_expired = time.time() - pending.get("created_at", 0) > PENDING_UPLOAD_TTL_SECONDS
    if is_expired or not os.path.exists(pending.get("path", "")):
        try:
            os.remove(pending.get("path", ""))
        except OSError:
            pass
        return None

    return pending


class StoredUpload:
    def __init__(self, path, filename):
        self.filename = filename
        self.stream = open(path, "rb")

    def read(self, *args, **kwargs):
        return self.stream.read(*args, **kwargs)

    def seek(self, *args, **kwargs):
        return self.stream.seek(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def close(self):
        self.stream.close()


def find_sf1_columns(df):
    header_row = None

    for index, row in df.head(12).iterrows():
        normalized = [str(value).upper().replace("\n", " ").strip() for value in row]

        if "LRN" in normalized and any("NAME" in value for value in normalized):
            header_row = index
            break

    if header_row is None:
        return None

    columns = {}

    for col, value in enumerate(df.iloc[header_row]):
        label = str(value).upper().replace("\n", " ").strip()

        if label == "LRN":
            columns["lrn"] = col
        elif "NAME" in label:
            columns["name"] = col
        elif "SEX" in label:
            columns["sex"] = col
        elif "REMARKS" in label:
            columns["remarks"] = col

    required = {"lrn", "name", "sex"}
    if not required.issubset(columns):
        return None

    next_row_index = header_row + 1
    data_start_row = header_row + 2

    if next_row_index < len(df):
        next_lrn = str(df.iloc[next_row_index, columns["lrn"]]).strip()
        next_lrn = re.sub(r"\.0$", "", next_lrn)
        if LRN_PATTERN.match(next_lrn):
            data_start_row = next_row_index

    columns["data_start_row"] = data_start_row
    return columns

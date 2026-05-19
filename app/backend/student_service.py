from .constants import PER_PAGE, SUPPORTED_GRADES
from .formatters import format_remarks, humanize_status
from .validators import is_valid_school_year


def build_student_record_filters(filters):
    where = " WHERE 1=1"
    params = []

    if filters.get("q"):
        where += " AND (s.lrn LIKE %s OR s.name LIKE %s)"
        search = f"%{filters['q']}%"
        params.extend([search, search])

    if filters.get("grade") and filters["grade"].isdigit() and int(filters["grade"]) in SUPPORTED_GRADES:
        where += " AND r.grade_level = %s"
        params.append(int(filters["grade"]))

    if filters.get("status"):
        where += " AND r.status = %s"
        params.append(filters["status"])

    if filters.get("year") and is_valid_school_year(filters["year"]):
        where += " AND r.school_year = %s"
        params.append(filters["year"])

    return where, params


def count_student_records(cursor, filters):
    where, params = build_student_record_filters(filters)
    query = """
        SELECT COUNT(*)
        FROM students s
        JOIN student_records r ON s.lrn = r.lrn
    """ + where
    cursor.execute(query, params)
    return cursor.fetchone()[0] or 0


def get_student_records(cursor, filters, page=1, per_page=PER_PAGE):
    where, params = build_student_record_filters(filters)
    offset = (page - 1) * per_page
    query = """
        SELECT s.lrn, s.name, r.gender, r.grade_level, r.school_year, r.status, r.remarks
        FROM students s
        JOIN student_records r ON s.lrn = r.lrn
    """ + where + " ORDER BY r.school_year DESC, r.grade_level, s.name LIMIT %s OFFSET %s"
    cursor.execute(query, params + [per_page, offset])

    return [
        {
            "lrn": lrn,
            "name": name,
            "gender": gender,
            "grade_level": grade_level,
            "school_year": school_year,
            "status": status,
            "status_label": humanize_status(status),
            "remarks": format_remarks(status, remarks),
        }
        for lrn, name, gender, grade_level, school_year, status, remarks in cursor.fetchall()
    ]


def get_school_years(cursor):
    cursor.execute(
        """
        SELECT DISTINCT school_year
        FROM student_records
        ORDER BY school_year DESC
        """
    )
    return [row[0] for row in cursor.fetchall()]


def get_report_records(cursor, school_year=""):
    query = """
        SELECT s.lrn, s.name, r.gender, r.grade_level, r.school_year, r.status, r.remarks
        FROM students s
        JOIN student_records r ON s.lrn = r.lrn
        WHERE 1=1
    """
    params = []

    if school_year:
        query += " AND r.school_year = %s"
        params.append(school_year)

    query += " ORDER BY r.school_year DESC, r.grade_level, s.name"
    cursor.execute(query, params)

    return [
        {
            "lrn": lrn,
            "name": name,
            "gender": gender or "",
            "grade_level": grade_level,
            "school_year": record_year,
            "status": status,
            "status_label": humanize_status(status),
            "remarks": format_remarks(status, remarks),
        }
        for lrn, name, gender, grade_level, record_year, status, remarks in cursor.fetchall()
    ]


def build_student_timelines(cursor):
    cursor.execute(
        """
        SELECT s.lrn, s.name, r.school_year, r.grade_level, r.status, r.remarks
        FROM students s
        JOIN student_records r ON s.lrn = r.lrn
        WHERE r.grade_level IN (7, 8, 9, 10)
        ORDER BY s.lrn, r.school_year, r.grade_level
        """
    )

    timelines = {}
    for lrn, name, school_year, grade_level, status, remarks in cursor.fetchall():
        timelines.setdefault(lrn, {"lrn": lrn, "name": name, "records": []})
        timelines[lrn]["records"].append(
            {
                "school_year": school_year,
                "grade_level": grade_level,
                "status": status,
                "status_label": humanize_status(status),
                "remarks": format_remarks(status, remarks),
            }
        )

    return timelines


def next_school_year(school_year):
    start, end = school_year.split("-")
    return f"{int(start) + 1}-{int(end) + 1}"


def get_at_risk_students(cursor):
    timelines = build_student_timelines(cursor)
    existing_years = set(get_school_years(cursor))
    at_risk = []

    for timeline in timelines.values():
        records = timeline["records"]
        latest = records[-1]

        if latest["status"] == "TRANSFER_OUT":
            at_risk.append(
                {
                    "lrn": timeline["lrn"],
                    "name": timeline["name"],
                    "last_grade": latest["grade_level"],
                    "last_year": latest["school_year"],
                    "reason": f"Transferred out after Grade {latest['grade_level']}",
                    "remarks": latest["remarks"] or "Transferred Out",
                }
            )
            continue

        if latest["grade_level"] < 10 and next_school_year(latest["school_year"]) in existing_years:
            if latest["status"] == "PENDING_TRANSFER_IN":
                reason = f"Pending Transfer-In / Needs Verification after Grade {latest['grade_level']}"
                remarks = latest["remarks"] or "Pending Transfer-In"
            else:
                reason = f"Did not appear after Grade {latest['grade_level']}"
                remarks = latest["remarks"] or "-"

            at_risk.append(
                {
                    "lrn": timeline["lrn"],
                    "name": timeline["name"],
                    "last_grade": latest["grade_level"],
                    "last_year": latest["school_year"],
                    "reason": reason,
                    "remarks": remarks,
                }
            )

    return at_risk


def get_csr_breakdown(at_risk_list):
    breakdown = {
        grade: {
            "grade": grade,
            "transferred_out": 0,
            "pending_verification": 0,
            "missing_after": 0,
            "total_left": 0,
        }
        for grade in SUPPORTED_GRADES
    }

    for student in at_risk_list:
        grade = student["last_grade"]
        if grade not in breakdown:
            continue

        if "Transferred out" in student["reason"]:
            breakdown[grade]["transferred_out"] += 1
        elif "Pending Transfer-In" in student["reason"]:
            breakdown[grade]["pending_verification"] += 1
        else:
            breakdown[grade]["missing_after"] += 1

        breakdown[grade]["total_left"] += 1

    return list(breakdown.values())


def log_change(cursor, lrn, field_name, old_value, new_value, school_year=None, grade_level=None, changed_by=None):
    old_text = "" if old_value is None else str(old_value).strip()
    new_text = "" if new_value is None else str(new_value).strip()

    if old_text == new_text:
        return False

    cursor.execute(
        """
        INSERT INTO student_change_logs
            (lrn, field_name, old_value, new_value, school_year, grade_level, changed_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (lrn, field_name, old_text, new_text, school_year, grade_level, changed_by),
    )
    return True


def upsert_student(cursor, lrn, name, gender, school_year, grade_level, changed_by=None):
    cursor.execute(
        """
        SELECT name, gender
        FROM students
        WHERE lrn = %s
        """,
        (lrn,),
    )
    existing = cursor.fetchone()

    if existing is None:
        cursor.execute(
            """
            INSERT INTO students (lrn, name, gender)
            VALUES (%s, %s, %s)
            """,
            (lrn, name, gender),
        )
        return 0

    old_name, old_gender = existing
    changes_logged = 0

    if log_change(cursor, lrn, "student.name_conflict", old_name, name, school_year, grade_level, changed_by):
        changes_logged += 1
    if log_change(cursor, lrn, "student.gender_conflict", old_gender, gender, school_year, grade_level, changed_by):
        changes_logged += 1

    return changes_logged


def upsert_student_record(cursor, lrn, school_year, grade_level, gender, status, remarks, changed_by=None):
    cursor.execute(
        """
        SELECT id, gender, status, remarks
        FROM student_records
        WHERE lrn = %s
        AND school_year = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (lrn, school_year),
    )
    existing = cursor.fetchone()

    if existing is None:
        cursor.execute(
            """
            INSERT INTO student_records (lrn, school_year, grade_level, gender, status, remarks)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (lrn, school_year, grade_level, gender, status, remarks),
        )
        return "inserted", 0

    record_id, old_gender, old_status, old_remarks = existing
    changes_logged = 0

    cursor.execute("SELECT grade_level FROM student_records WHERE id = %s", (record_id,))
    old_grade = cursor.fetchone()[0]
    if log_change(cursor, lrn, "record.grade_level", old_grade, grade_level, school_year, grade_level, changed_by):
        changes_logged += 1
    if log_change(cursor, lrn, "record.gender", old_gender, gender, school_year, grade_level, changed_by):
        changes_logged += 1
    if log_change(cursor, lrn, "record.status", old_status, status, school_year, grade_level, changed_by):
        changes_logged += 1
    if log_change(cursor, lrn, "record.remarks", old_remarks, remarks, school_year, grade_level, changed_by):
        changes_logged += 1

    cursor.execute(
        """
        UPDATE student_records
        SET grade_level = %s, gender = %s, status = %s, remarks = %s
        WHERE id = %s
        """,
        (grade_level, gender, status, remarks, record_id),
    )
    return "updated", changes_logged

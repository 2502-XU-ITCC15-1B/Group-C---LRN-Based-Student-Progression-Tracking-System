from .constants import SUPPORTED_GRADES
from .formatters import format_remarks, humanize_status


def next_school_year(school_year):
    start, end = school_year.split("-")
    return f"{int(start) + 1}-{int(end) + 1}"


def build_expected_path(start_year, start_grade):
    path = []
    current_year = start_year

    for grade in range(start_grade, 11):
        path.append({"grade": grade, "school_year": current_year})
        current_year = next_school_year(current_year)

    return path


def summarize_cohort_status(path_cells, records=None):
    records = records or []
    statuses = [cell["status"] for cell in path_cells if cell["status"] != "MISSING"]
    all_statuses = statuses + [record[2] for record in records]
    grades = [cell["actual_grade"] for cell in path_cells if cell["actual_grade"] is not None]
    all_grades = [record[1] for record in records]
    has_missing = any(cell["status"] == "MISSING" for cell in path_cells)
    has_grade_10 = 10 in all_grades or (
        path_cells and path_cells[-1]["status"] != "MISSING" and path_cells[-1]["expected_grade"] == 10
    )

    if "TRANSFER_OUT" in all_statuses:
        return "TRANSFER_OUT"

    if has_grade_10:
        return "DELAYED_COMPLETED" if has_missing else "COMPLETED"

    if "TRANSFER_IN" in all_statuses or "PENDING_TRANSFER_IN" in all_statuses:
        return "TRANSFER_IN"

    if len(grades) != len(set(grades)) or len(all_grades) != len(set(all_grades)):
        return "REPEATED"

    if has_missing:
        return "INCOMPLETE"

    return "STRAIGHT_PATH"


def build_cohort_tracking(cursor, start_year, start_grade, expected_path):
    cursor.execute(
        """
        SELECT DISTINCT s.lrn, s.name
        FROM students s
        JOIN student_records r ON s.lrn = r.lrn
        WHERE r.school_year = %s
        AND r.grade_level = %s
        ORDER BY s.name
        """,
        (start_year, start_grade),
    )
    cohort_students = cursor.fetchall()

    rows = []
    summary = {
        "total": len(cohort_students),
        "straight_path": 0,
        "completed": 0,
        "delayed": 0,
        "repeated": 0,
        "transfer_in": 0,
        "transfer_out": 0,
        "dropped": 0,
        "incomplete": 0,
        "for_review": 0,
        "path_counts": {},
    }

    for lrn, name in cohort_students:
        cursor.execute(
            """
            SELECT school_year, grade_level, status, remarks
            FROM student_records
            WHERE lrn = %s
            AND grade_level BETWEEN %s AND 10
            ORDER BY school_year, grade_level
            """,
            (lrn, start_grade),
        )
        records = cursor.fetchall()
        records_by_year_grade = {
            (school_year, grade_level): {
                "status": status,
                "remarks": remarks,
                "grade": grade_level,
            }
            for school_year, grade_level, status, remarks in records
        }

        path_cells = []

        for step in expected_path:
            record = records_by_year_grade.get((step["school_year"], step["grade"]))

            if record:
                path_cells.append(
                    {
                        "school_year": step["school_year"],
                        "expected_grade": step["grade"],
                        "actual_grade": record["grade"],
                        "status": record["status"],
                        "status_label": humanize_status(record["status"]),
                        "remarks": format_remarks(record["status"], record["remarks"]),
                    }
                )
            else:
                path_cells.append(
                    {
                        "school_year": step["school_year"],
                        "expected_grade": step["grade"],
                        "actual_grade": None,
                        "status": "MISSING",
                        "status_label": humanize_status("MISSING"),
                        "remarks": "",
                    }
                )

        result = summarize_cohort_status(path_cells, records)

        if result == "COMPLETED":
            summary["completed"] += 1
            summary["straight_path"] += 1
        elif result == "DELAYED_COMPLETED":
            summary["completed"] += 1
            summary["delayed"] += 1
        elif result == "STRAIGHT_PATH":
            summary["straight_path"] += 1
        elif result == "REPEATED":
            summary["repeated"] += 1
        elif result == "TRANSFER_IN":
            summary["transfer_in"] += 1
        elif result == "TRANSFER_OUT":
            summary["transfer_out"] += 1
        else:
            summary["incomplete"] += 1

        if result == "INCOMPLETE" and any(cell["status"] == "MISSING" for cell in path_cells):
            summary["dropped"] += 1

        if result not in {"COMPLETED", "STRAIGHT_PATH"}:
            summary["for_review"] += 1

        rows.append(
            {
                "lrn": lrn,
                "name": name,
                "path": path_cells,
                "result": result,
                "result_label": humanize_status(result),
            }
        )

    for step in expected_path:
        summary["path_counts"][f"{step['grade']}|{step['school_year']}"] = sum(
            1
            for row in rows
            for cell in row["path"]
            if cell["expected_grade"] == step["grade"]
            and cell["school_year"] == step["school_year"]
            and cell["status"] not in {"MISSING", "TRANSFER_OUT"}
        )

    return rows, summary


def build_grade7_cohort_report(cursor, start_year, start_grade=7):
    expected_path = build_expected_path(start_year, start_grade)
    end_year = expected_path[-1]["school_year"]

    cursor.execute(
        """
        SELECT DISTINCT s.lrn, s.name
        FROM students s
        JOIN student_records r ON s.lrn = r.lrn
        WHERE r.school_year = %s
        AND r.grade_level = %s
        ORDER BY s.name
        """,
        (start_year, start_grade),
    )
    cohort_students = cursor.fetchall()

    summary = {
        "total": len(cohort_students),
        "on_time": 0,
        "completed": 0,
        "delayed": 0,
        "repeated": 0,
        "transfer_out": 0,
        "incomplete": 0,
        "for_review": 0,
    }
    breakdown = {
        grade: {
            "grade": grade,
            "missing": 0,
            "repeated_or_delayed": 0,
            "transferred_out": 0,
            "for_review": 0,
        }
        for grade in SUPPORTED_GRADES
    }
    review_learners = []
    rows = []

    for lrn, name in cohort_students:
        cursor.execute(
            """
            SELECT school_year, grade_level, status, remarks
            FROM student_records
            WHERE lrn = %s
            AND grade_level BETWEEN %s AND 10
            ORDER BY school_year, grade_level
            """,
            (lrn, start_grade),
        )
        records = [
            {
                "school_year": school_year,
                "grade_level": grade_level,
                "status": status,
                "remarks": remarks,
            }
            for school_year, grade_level, status, remarks in cursor.fetchall()
        ]
        records_by_year_grade = {
            (record["school_year"], record["grade_level"]): record
            for record in records
        }
        grade_years = {}
        for record in records:
            grade_years.setdefault(record["grade_level"], set()).add(record["school_year"])

        path_cells = []
        missing_steps = []
        for step in expected_path:
            record = records_by_year_grade.get((step["school_year"], step["grade"]))
            if record:
                path_cells.append(
                    {
                        "school_year": step["school_year"],
                        "expected_grade": step["grade"],
                        "status": record["status"],
                        "status_label": humanize_status(record["status"]),
                        "remarks": format_remarks(record["status"], record["remarks"]),
                    }
                )
            else:
                missing_steps.append(step)
                path_cells.append(
                    {
                        "school_year": step["school_year"],
                        "expected_grade": step["grade"],
                        "status": "MISSING",
                        "status_label": humanize_status("MISSING"),
                        "remarks": "",
                    }
                )

        has_grade10 = any(record["grade_level"] == 10 for record in records)
        has_expected_grade10 = (end_year, 10) in records_by_year_grade
        has_transfer_out = any(record["status"] == "TRANSFER_OUT" for record in records)
        has_pending_transfer = any(record["status"] == "PENDING_TRANSFER_IN" for record in records)
        has_repetition = any(len(years) > 1 for years in grade_years.values())
        on_time = has_expected_grade10 and not missing_steps and not has_transfer_out
        delayed = has_grade10 and not on_time

        if has_transfer_out:
            result = "TRANSFER_OUT"
            reason = "Transferred out before completing the expected path"
        elif on_time:
            result = "COMPLETED"
            reason = ""
        elif delayed:
            result = "DELAYED_COMPLETED"
            reason = "Reached Grade 10 later than the expected path"
        elif has_repetition:
            result = "REPEATED"
            reason = "Repeated or delayed in one grade level"
        else:
            result = "INCOMPLETE"
            reason = "Missing expected progression record"

        if has_pending_transfer and result not in {"COMPLETED", "DELAYED_COMPLETED"}:
            reason = "Pending transfer-in record needs verification"

        if on_time:
            summary["on_time"] += 1
        if has_grade10:
            summary["completed"] += 1
        if delayed or has_repetition:
            summary["delayed"] += 1
        if has_repetition:
            summary["repeated"] += 1
        if has_transfer_out:
            summary["transfer_out"] += 1
        if result == "INCOMPLETE":
            summary["incomplete"] += 1
        if result in {"TRANSFER_OUT", "REPEATED", "INCOMPLETE", "DELAYED_COMPLETED"} or has_pending_transfer:
            summary["for_review"] += 1

        if has_transfer_out:
            transfer_record = next((record for record in records if record["status"] == "TRANSFER_OUT"), records[-1])
            grade = transfer_record["grade_level"]
            breakdown[grade]["transferred_out"] += 1
            breakdown[grade]["for_review"] += 1
        if has_repetition or delayed:
            latest_grade = max(record["grade_level"] for record in records)
            breakdown[latest_grade]["repeated_or_delayed"] += 1
            breakdown[latest_grade]["for_review"] += 1
        if missing_steps and not has_grade10 and not has_transfer_out:
            grade = missing_steps[0]["grade"]
            breakdown[grade]["missing"] += 1
            breakdown[grade]["for_review"] += 1

        if reason:
            latest = records[-1] if records else {"grade_level": start_grade, "school_year": start_year, "remarks": ""}
            review_learners.append(
                {
                    "lrn": lrn,
                    "name": name,
                    "last_grade": latest["grade_level"],
                    "last_year": latest["school_year"],
                    "reason": reason,
                    "remarks": format_remarks(latest.get("status"), latest.get("remarks")) or "-",
                }
            )

        rows.append(
            {
                "lrn": lrn,
                "name": name,
                "path": path_cells,
                "result": result,
                "result_label": humanize_status(result),
                "reason": reason,
            }
        )

    rates = {
        "on_time_completion": round((summary["on_time"] / summary["total"]) * 100, 2) if summary["total"] else 0,
        "overall_completion": round((summary["completed"] / summary["total"]) * 100, 2) if summary["total"] else 0,
    }

    return {
        "start_year": start_year,
        "end_year": end_year,
        "start_grade": start_grade,
        "title": f"Grade {start_grade} Entry Cohort Progression Report: {start_year} to {end_year}",
        "expected_path": expected_path,
        "summary": summary,
        "rates": rates,
        "breakdown": list(breakdown.values()),
        "review_learners": review_learners,
        "rows": rows,
    }

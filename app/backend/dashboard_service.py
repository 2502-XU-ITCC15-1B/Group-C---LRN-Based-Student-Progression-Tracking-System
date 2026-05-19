from .constants import SUPPORTED_GRADES


def get_dashboard_stats(cursor):
    cursor.execute("SELECT COUNT(*) FROM students")
    total_students = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM student_records")
    total_records = cursor.fetchone()[0] or 0

    cursor.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'ENROLLED' THEN 1 ELSE 0 END),
            SUM(CASE WHEN status = 'TRANSFER_IN' THEN 1 ELSE 0 END),
            SUM(CASE WHEN status = 'PENDING_TRANSFER_IN' THEN 1 ELSE 0 END),
            SUM(CASE WHEN status = 'TRANSFER_OUT' THEN 1 ELSE 0 END)
        FROM student_records
        """
    )
    enrolled, transfer_in, pending_transfer_in, transfer_out = cursor.fetchone()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT lrn, grade_level
            FROM student_records
            GROUP BY lrn, grade_level
            HAVING COUNT(DISTINCT school_year) > 1
        ) repeated
        """
    )
    repeaters = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(DISTINCT lrn) FROM student_records WHERE grade_level = 10")
    grade_10 = cursor.fetchone()[0] or 0

    return {
        "total_students": total_students,
        "total_records": total_records,
        "active_students": enrolled or 0,
        "transfer_in": transfer_in or 0,
        "pending_transfer_in": pending_transfer_in or 0,
        "transfer_out": transfer_out or 0,
        "repeaters": repeaters,
        "completed": grade_10,
    }


def get_grade_distribution(cursor):
    cursor.execute(
        """
        SELECT grade_level, COUNT(DISTINCT lrn)
        FROM student_records
        WHERE grade_level IN (7, 8, 9, 10)
        GROUP BY grade_level
        """
    )
    counts = {grade: count for grade, count in cursor.fetchall()}

    return [
        {
            "grade": grade,
            "count": counts.get(grade, 0),
            "label": "Tracked" if counts.get(grade, 0) else "No records",
        }
        for grade in SUPPORTED_GRADES
    ]


def get_recent_changes(cursor, limit=5):
    limit = max(1, min(int(limit), 20))
    cursor.execute(
        f"""
        SELECT lrn, field_name, old_value, new_value, school_year, grade_level, changed_at
        FROM student_change_logs
        ORDER BY changed_at DESC
        LIMIT {limit}
        """
    )

    return [
        {
            "lrn": lrn,
            "field_name": field_name,
            "old_value": old_value,
            "new_value": new_value,
            "school_year": school_year,
            "grade_level": grade_level,
            "changed_at": changed_at,
        }
        for lrn, field_name, old_value, new_value, school_year, grade_level, changed_at in cursor.fetchall()
    ]

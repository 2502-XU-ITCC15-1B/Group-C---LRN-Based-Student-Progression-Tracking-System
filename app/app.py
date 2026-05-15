from flask import Flask, flash, redirect, render_template, request, send_file, send_from_directory, session, url_for
from functools import wraps
from io import BytesIO
import mysql.connector
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
import os
import pandas as pd
import re
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")

SUPPORTED_GRADES = [7, 8, 9, 10]
SCHOOL_YEAR_PATTERN = re.compile(r"^\d{4}-\d{4}$")
LRN_PATTERN = re.compile(r"^\d{12}$")
SCHEMA_READY = False


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access the system.")
            return redirect(url_for("login", next=request.path))

        return view(*args, **kwargs)

    return wrapped_view


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access the system.")
            return redirect(url_for("login", next=request.path))

        if session.get("role") != "admin":
            flash("You do not have permission to perform that action.")
            return redirect(url_for("dashboard"))

        return view(*args, **kwargs)

    return wrapped_view


@app.context_processor
def inject_current_user():
    return {"current_user": session.get("username"), "current_role": session.get("role")}


EDITABLE_RECORD_STATUSES = ["ENROLLED", "TRANSFER_IN", "PENDING_TRANSFER_IN", "TRANSFER_OUT"]


@app.route("/")
def root():
    return redirect(url_for("dashboard"))


@app.route("/legacy-dashboard")
@login_required
def home():
    ensure_schema()

    conn = get_db_connection()
    cursor = conn.cursor()

    grade = request.args.get("grade")
    year = request.args.get("year")

    base_query = """
        FROM students s
        JOIN student_records r ON s.lrn = r.lrn
        WHERE 1=1
    """

    params = []

    if grade and grade.isdigit() and int(grade) in SUPPORTED_GRADES:
        base_query += " AND r.grade_level = %s"
        params.append(int(grade))

    if year:
        base_query += " AND r.school_year = %s"
        params.append(year)

    cursor.execute(
        """
        SELECT s.lrn, s.name, r.gender, r.grade_level, r.school_year, r.status, r.remarks
        """
        + base_query
        + """
        ORDER BY r.school_year, r.grade_level, s.name
        """,
        params,
    )
    students = cursor.fetchall()

    cursor.execute(
        """
        SELECT lrn, field_name, old_value, new_value, school_year, grade_level, changed_at
        FROM student_change_logs
        ORDER BY changed_at DESC
        LIMIT 10
        """
    )
    recent_changes = cursor.fetchall()

    cursor.execute(
        """
        SELECT
            COUNT(*),
            SUM(CASE WHEN UPPER(r.gender) LIKE 'M%' THEN 1 ELSE 0 END),
            SUM(CASE WHEN UPPER(r.gender) LIKE 'F%' THEN 1 ELSE 0 END),
            SUM(CASE WHEN r.status = 'TRANSFER_IN' THEN 1 ELSE 0 END),
            SUM(CASE WHEN r.status = 'PENDING_TRANSFER_IN' THEN 1 ELSE 0 END),
            SUM(CASE WHEN r.status = 'TRANSFER_OUT' THEN 1 ELSE 0 END)
        """
        + base_query,
        params,
    )
    result = cursor.fetchone()

    total = result[0]
    male = result[1] or 0
    female = result[2] or 0
    transfer_in = result[3] or 0
    pending_transfer_in = result[4] or 0
    transfer_out = result[5] or 0

    male_pct = round((male / total) * 100, 2) if total > 0 else 0
    female_pct = round((female / total) * 100, 2) if total > 0 else 0

    cursor.close()
    conn.close()

    school_years = get_school_years_for_computation()

    retention = {"rate": 0, "retained": 0, "dropped": 0}
    promotion = {"rate": 0, "promoted": 0, "repeated": 0, "dropped": 0}

    if len(school_years) >= 2:
        latest_year = school_years[0]
        previous_year = school_years[1]
        try:
            retention = compute_retention(previous_year, latest_year)
        except Exception:
            pass
        try:
            promotion = compute_promotion(previous_year, latest_year, 9, 10)
        except Exception:
            pass
    else:
        retention = {"rate": 0, "retained": 0, "dropped": 0}
        promotion = {"rate": 0, "promoted": 0, "repeated": 0, "dropped": 0}

    return render_template(
        "overview.html",
        students=students,
        total=total,
        male=male,
        female=female,
        transfer_in=transfer_in,
        pending_transfer_in=pending_transfer_in,
        transfer_out=transfer_out,
        male_pct=male_pct,
        female_pct=female_pct,
        retention=retention,
        promotion=promotion,
        grades=SUPPORTED_GRADES,
        recent_changes=recent_changes,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    ensure_schema()

    if "user_id" in session and request.method == "GET":
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, username, password_hash, role, is_active
            FROM users
            WHERE username = %s
            """,
            (username,),
        )
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if not user or not user["is_active"] or not check_password_hash(user["password_hash"], password):
            flash("Invalid username or password.")
            return render_template("login.html", username=username)

        session.clear()
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["role"] = user["role"]
        flash("Logged in successfully.")
        next_page = request.args.get("next")
        if not next_page or not next_page.startswith("/") or next_page.startswith("//"):
            next_page = url_for("dashboard")
        return redirect(next_page)

    return render_template("login.html")


@app.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    if request.method == "POST":
        session.clear()
        flash("Logged out successfully.")
        return redirect(url_for("login"))
    return render_template("logout_confirm.html")


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    ensure_schema()

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(new_password) < 8:
            flash("New password must be at least 8 characters.")
            return render_template("change_password.html")

        if new_password != confirm_password:
            flash("New password and confirmation do not match.")
            return render_template("change_password.html")

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, password_hash
            FROM users
            WHERE id = %s
            """,
            (session["user_id"],),
        )
        user = cursor.fetchone()

        if not user or not check_password_hash(user["password_hash"], current_password):
            cursor.close()
            conn.close()
            flash("Current password is incorrect.")
            return render_template("change_password.html")

        cursor.execute(
            """
            UPDATE users
            SET password_hash = %s
            WHERE id = %s
            """,
            (generate_password_hash(new_password), session["user_id"]),
        )
        conn.commit()
        cursor.close()
        conn.close()
        flash("Password changed successfully.")
        return redirect(url_for("dashboard"))

    return render_template("change_password.html")


@app.route("/dashboard")
@login_required
def dashboard():
    ensure_schema()

    conn = get_db_connection()
    cursor = conn.cursor()
    stats = get_dashboard_stats(cursor)
    recent_changes = get_recent_changes(cursor, limit=5)
    grade_distribution = get_grade_distribution(cursor)
    cursor.close()
    conn.close()

    return render_template(
        "dashboard.html",
        stats=stats,
        recent_changes=recent_changes,
        grade_distribution=grade_distribution,
    )


@app.route("/lis-upload")
@login_required
def lis_upload():
    return render_template("lis_upload.html", grades=SUPPORTED_GRADES)


@app.route("/records/delete-batch", methods=["POST"])
@admin_required
def delete_batch_records():
    ensure_schema()

    school_year = request.form.get("school_year", "").strip()
    grade_level = request.form.get("grade_level", "").strip()
    confirmation = request.form.get("confirmation", "").strip()

    if not SCHOOL_YEAR_PATTERN.match(school_year):
        flash("Use school year format YYYY-YYYY before deleting a batch.")
        return redirect(url_for("lis_upload"))

    if not grade_level.isdigit() or int(grade_level) not in SUPPORTED_GRADES:
        flash("Select a valid Grade 7 to Grade 10 batch to delete.")
        return redirect(url_for("lis_upload"))

    if confirmation != "DELETE":
        flash("Type DELETE to confirm batch deletion.")
        return redirect(url_for("lis_upload"))

    grade_level = int(grade_level)
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM student_records
        WHERE school_year = %s
        AND grade_level = %s
        """,
        (school_year, grade_level),
    )
    record_count = cursor.fetchone()[0] or 0

    if record_count == 0:
        cursor.close()
        conn.close()
        flash(f"No Grade {grade_level} records found for {school_year}.")
        return redirect(url_for("lis_upload"))

    cursor.execute(
        """
        DELETE FROM student_change_logs
        WHERE school_year = %s
        AND grade_level = %s
        """,
        (school_year, grade_level),
    )

    cursor.execute(
        """
        DELETE FROM student_records
        WHERE school_year = %s
        AND grade_level = %s
        """,
        (school_year, grade_level),
    )

    cursor.execute(
        """
        DELETE l
        FROM student_change_logs l
        LEFT JOIN student_records r ON l.lrn = r.lrn
        WHERE r.lrn IS NULL
        """
    )

    cursor.execute(
        """
        DELETE s
        FROM students s
        LEFT JOIN student_records r ON s.lrn = r.lrn
        WHERE r.lrn IS NULL
        """
    )

    conn.commit()
    cursor.close()
    conn.close()

    flash(f"Deleted {record_count} Grade {grade_level} records for {school_year}.")
    return redirect(url_for("lis_upload"))


@app.route("/records/delete-all", methods=["POST"])
@admin_required
def delete_all_records():
    ensure_schema()

    confirmation = request.form.get("confirmation", "").strip()

    if confirmation != "DELETE ALL":
        flash("Type DELETE ALL to confirm overall deletion.")
        return redirect(url_for("lis_upload"))

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT COUNT(*) FROM student_records")
        record_count = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COUNT(*) FROM students")
        student_count = cursor.fetchone()[0] or 0

        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        cursor.execute("TRUNCATE TABLE student_change_logs")
        cursor.execute("TRUNCATE TABLE student_records")
        cursor.execute("TRUNCATE TABLE students")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

        conn.commit()
    except Exception:
        conn.rollback()
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        conn.commit()
        raise
    finally:
        cursor.close()
        conn.close()

    flash(f"Deleted all records: {student_count} students and {record_count} grade records.")
    return redirect(url_for("lis_upload"))


@app.route("/students")
@login_required
def students():
    ensure_schema()

    filters = {
        "q": request.args.get("q", "").strip(),
        "grade": request.args.get("grade", "").strip(),
        "status": request.args.get("status", "").strip(),
        "year": request.args.get("year", "").strip(),
    }

    conn = get_db_connection()
    cursor = conn.cursor()
    records = get_student_records(cursor, filters)
    stats = get_dashboard_stats(cursor)
    cursor.close()
    conn.close()

    return render_template(
        "records_page.html",
        records=records,
        grades=SUPPORTED_GRADES,
        statuses=EDITABLE_RECORD_STATUSES,
        filters=filters,
        stats=stats,
    )


@app.route("/students/add", methods=["POST"])
@admin_required
def add_student():
    ensure_schema()

    lrn = request.form.get("lrn", "").strip()
    name = request.form.get("name", "").strip()
    gender = normalize_gender(request.form.get("gender", ""))
    school_year = request.form.get("school_year", "").strip()
    grade_level = request.form.get("grade_level", "").strip()
    status = request.form.get("status", "").strip()
    remarks = normalize_remarks(request.form.get("remarks", ""))

    if not LRN_PATTERN.match(lrn):
        flash("Enter a valid 12-digit LRN before adding a student.")
        return redirect(url_for("students"))

    if not name:
        flash("Student name is required.")
        return redirect(url_for("students"))

    if gender not in {"MALE", "FEMALE"}:
        flash("Select a valid sex value.")
        return redirect(url_for("students"))

    if not SCHOOL_YEAR_PATTERN.match(school_year):
        flash("Use school year format YYYY-YYYY before adding a student.")
        return redirect(url_for("students"))

    if not grade_level.isdigit() or int(grade_level) not in SUPPORTED_GRADES:
        flash("Select a valid Grade 7 to Grade 10 record.")
        return redirect(url_for("students"))

    if status not in EDITABLE_RECORD_STATUSES:
        flash("Select a valid student status.")
        return redirect(url_for("students"))

    grade_level = int(grade_level)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id
        FROM student_records
        WHERE lrn = %s
        AND school_year = %s
        AND grade_level = %s
        LIMIT 1
        """,
        (lrn, school_year, grade_level),
    )

    if cursor.fetchone():
        cursor.close()
        conn.close()
        flash(f"A Grade {grade_level} record for {lrn} already exists in {school_year}.")
        return redirect(url_for("students"))

    changed_by = session.get("username")
    changes_logged = upsert_student(cursor, lrn, name, gender, school_year, grade_level, changed_by)
    record_action, record_changes = upsert_student_record(
        cursor,
        lrn,
        school_year,
        grade_level,
        gender,
        status,
        remarks,
        changed_by,
    )
    changes_logged += record_changes
    log_change(cursor, lrn, "manual.student_record", "", f"Added Grade {grade_level} record for {school_year}", school_year, grade_level, changed_by)
    conn.commit()
    cursor.close()
    conn.close()

    flash(f"Added {name} with {record_action} Grade {grade_level} record. Logged {changes_logged + 1} change(s).")
    return redirect(url_for("student_history", lrn=lrn))


@app.route("/cohort-tracking")
@login_required
def cohort_tracking_frontend():
    return cohort_tracking()


@app.route("/reports")
@login_required
def reports():
    ensure_schema()

    selected_year = request.args.get("school_year", "").strip()
    base_year = request.args.get("base_year", "").strip()
    compare_year = request.args.get("compare_year", "").strip()
    grade_from = request.args.get("grade_from", "7").strip()
    grade_to = request.args.get("grade_to", "8").strip()
    calculator = None
    conn = get_db_connection()
    cursor = conn.cursor()
    stats = get_dashboard_stats(cursor)
    grade_distribution = get_grade_distribution(cursor)
    school_years = get_school_years(cursor)
    at_risk_students = get_at_risk_students(cursor)
    csr_breakdown = get_csr_breakdown(at_risk_students)

    if (
        SCHOOL_YEAR_PATTERN.match(base_year)
        and SCHOOL_YEAR_PATTERN.match(compare_year)
        and grade_from.isdigit()
        and grade_to.isdigit()
        and int(grade_from) in SUPPORTED_GRADES
        and int(grade_to) in SUPPORTED_GRADES
    ):
        calculator = build_year_grade_calculator(
            cursor,
            base_year,
            compare_year,
            int(grade_from),
            int(grade_to),
        )

    cursor.close()
    conn.close()

    return render_template(
        "reports.html",
        stats=stats,
        grade_distribution=grade_distribution,
        school_years=school_years,
        selected_year=selected_year,
        at_risk_students=at_risk_students,
        csr_breakdown=csr_breakdown,
        calculator=calculator,
        base_year=base_year,
        compare_year=compare_year,
        grade_from=int(grade_from) if grade_from.isdigit() else 7,
        grade_to=int(grade_to) if grade_to.isdigit() else 8,
    )


@app.route("/reports/export")
@login_required
def export_report():
    ensure_schema()

    school_year = request.args.get("school_year", "").strip()
    if school_year and not SCHOOL_YEAR_PATTERN.match(school_year):
        flash("Use school year format YYYY-YYYY before exporting.")
        return redirect(url_for("reports"))

    conn = get_db_connection()
    cursor = conn.cursor()
    stats = get_dashboard_stats(cursor)
    grade_distribution = get_grade_distribution(cursor)
    records = get_report_records(cursor, school_year)
    at_risk_students = get_at_risk_students(cursor)
    csr_breakdown = get_csr_breakdown(at_risk_students)
    cursor.close()
    conn.close()

    workbook = build_progression_report_workbook(
        stats,
        grade_distribution,
        records,
        school_year,
        at_risk_students,
        csr_breakdown,
    )
    output = BytesIO()
    workbook.save(output)
    output.seek(0)

    year_label = school_year or "all-years"
    filename = f"lrn-progression-report-{year_label}.xlsx"

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/reports/print")
@login_required
def print_report():
    ensure_schema()

    school_year = request.args.get("school_year", "").strip()
    if school_year and not SCHOOL_YEAR_PATTERN.match(school_year):
        flash("Use school year format YYYY-YYYY before printing.")
        return redirect(url_for("reports"))

    conn = get_db_connection()
    cursor = conn.cursor()
    stats = get_dashboard_stats(cursor)
    grade_distribution = get_grade_distribution(cursor)
    records = get_report_records(cursor, school_year)
    at_risk_students = get_at_risk_students(cursor)
    csr_breakdown = get_csr_breakdown(at_risk_students)
    cursor.close()
    conn.close()

    return render_template(
        "print_report.html",
        stats=stats,
        grade_distribution=grade_distribution,
        records=records,
        selected_year=school_year,
        report_scope=school_year or "All Uploaded School Years",
        at_risk_students=at_risk_students,
        csr_breakdown=csr_breakdown,
    )


@app.route("/computations")
@login_required
def computations():
    return redirect(url_for("reports"))


@app.route("/users")
@admin_required
def users():
    return redirect(url_for("dashboard"))


@app.route("/templates/<path:filename>")
def template_assets(filename):
    return send_from_directory("templates", filename)


@app.route("/rsc/<path:filename>")
def resources(filename):
    return send_from_directory("rsc", filename)


@app.route("/student/search")
@login_required
def search_student():
    lrn = request.args.get("lrn", "").strip()

    if not LRN_PATTERN.match(lrn):
        flash("Enter a valid 12-digit LRN to view student history.")
        return redirect(url_for("students"))

    return redirect(url_for("student_history", lrn=lrn))


@app.route("/student/<lrn>")
@login_required
def student_history(lrn):
    ensure_schema()

    if not LRN_PATTERN.match(lrn):
        flash("Invalid LRN. Use exactly 12 digits.")
        return redirect(url_for("students"))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            s.lrn,
            s.name,
            COALESCE(
                s.gender,
                (
                    SELECT r.gender
                    FROM student_records r
                    WHERE r.lrn = s.lrn
                    ORDER BY r.school_year DESC, r.grade_level DESC
                    LIMIT 1
                )
            ),
            s.updated_at
        FROM students s
        WHERE s.lrn = %s
        """,
        (lrn,),
    )
    student = cursor.fetchone()

    if student is None:
        cursor.close()
        conn.close()
        flash("No student found for that LRN.")
        return redirect(url_for("students"))

    cursor.execute(
        """
        SELECT school_year, grade_level, gender, status, remarks, updated_at
        FROM student_records
        WHERE lrn = %s
        ORDER BY school_year, grade_level
        """,
        (lrn,),
    )
    history = [
        {
            "school_year": school_year,
            "grade_level": grade_level,
            "gender": gender,
            "status": status,
            "status_label": humanize_status(status),
            "remarks": format_remarks(status, remarks),
            "raw_remarks": normalize_remarks(remarks),
            "updated_at": updated_at,
        }
        for school_year, grade_level, gender, status, remarks, updated_at in cursor.fetchall()
    ]

    cursor.execute(
        """
        SELECT field_name, old_value, new_value, school_year, grade_level, changed_by, changed_at
        FROM student_change_logs
        WHERE lrn = %s
        ORDER BY changed_at DESC
        """,
        (lrn,),
    )
    changes = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "student_history.html",
        student=student,
        history=history,
        changes=changes,
        editable_statuses=EDITABLE_RECORD_STATUSES
    )


@app.route("/student/<lrn>/record/update", methods=["POST"])
@admin_required
def update_student_record(lrn):
    ensure_schema()

    if not LRN_PATTERN.match(lrn):
        flash("Invalid LRN. Use exactly 12 digits.")
        return redirect(url_for("students"))

    school_year = request.form.get("school_year", "").strip()
    grade_level = request.form.get("grade_level", "").strip()
    status = request.form.get("status", "").strip()
    remarks = normalize_remarks(request.form.get("remarks", ""))

    if not SCHOOL_YEAR_PATTERN.match(school_year):
        flash("Use school year format YYYY-YYYY before updating a record.")
        return redirect(url_for("student_history", lrn=lrn))

    if not grade_level.isdigit() or int(grade_level) not in SUPPORTED_GRADES:
        flash("Select a valid Grade 7 to Grade 10 record.")
        return redirect(url_for("student_history", lrn=lrn))

    if status not in EDITABLE_RECORD_STATUSES:
        flash("Select a valid student status.")
        return redirect(url_for("student_history", lrn=lrn))

    grade_level = int(grade_level)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, status, remarks
        FROM student_records
        WHERE lrn = %s
        AND school_year = %s
        AND grade_level = %s
        LIMIT 1
        """,
        (lrn, school_year, grade_level),
    )
    existing = cursor.fetchone()

    if existing is None:
        cursor.close()
        conn.close()
        flash("No matching progression record was found.")
        return redirect(url_for("student_history", lrn=lrn))

    record_id, old_status, old_remarks = existing
    changes_logged = 0
    changed_by = session.get("username")

    if log_change(cursor, lrn, "record.status", old_status, status, school_year, grade_level, changed_by=changed_by):
        changes_logged += 1
    if log_change(cursor, lrn, "record.remarks", old_remarks, remarks, school_year, grade_level, changed_by=changed_by):
        changes_logged += 1

    if changes_logged:
        cursor.execute(
            """
            UPDATE student_records
            SET status = %s, remarks = %s
            WHERE id = %s
            """,
            (status, remarks, record_id),
        )
        conn.commit()
        flash("Student record updated and logged.")
    else:
        flash("No changes were made.")

    cursor.close()
    conn.close()
    return redirect(url_for("student_history", lrn=lrn))


@app.route("/student/<lrn>/delete", methods=["POST"])
@admin_required
def delete_student(lrn):
    ensure_schema()

    if not LRN_PATTERN.match(lrn):
        flash("Invalid LRN. Use exactly 12 digits.")
        return redirect(url_for("students"))

    confirmation = request.form.get("confirmation", "").strip()
    if confirmation != "DELETE STUDENT":
        flash("Type DELETE STUDENT to confirm student deletion.")
        return redirect(url_for("student_history", lrn=lrn))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM students WHERE lrn = %s", (lrn,))
    student = cursor.fetchone()

    if student is None:
        cursor.close()
        conn.close()
        flash("No student found for that LRN.")
        return redirect(url_for("students"))

    cursor.execute("SELECT COUNT(*) FROM student_records WHERE lrn = %s", (lrn,))
    record_count = cursor.fetchone()[0] or 0

    cursor.execute("DELETE FROM student_change_logs WHERE lrn = %s", (lrn,))
    cursor.execute("DELETE FROM student_records WHERE lrn = %s", (lrn,))
    cursor.execute("DELETE FROM students WHERE lrn = %s", (lrn,))
    conn.commit()
    cursor.close()
    conn.close()

    flash(f"Deleted {student[0]} and {record_count} progression record(s).")
    return redirect(url_for("students"))


@app.route("/cohort")
@login_required
def cohort_tracking():
    ensure_schema()

    start_year = request.args.get("start_year", "").strip()
    start_grade = request.args.get("start_grade", "7").strip()
    cohort_rows = []
    summary = {
        "total": 0,
        "straight_path": 0,
        "completed": 0,
        "repeated": 0,
        "transfer_in": 0,
        "transfer_out": 0,
        "dropped": 0,
        "incomplete": 0,
    }
    expected_path = []

    if start_year:
        if not SCHOOL_YEAR_PATTERN.match(start_year):
            flash("Use school year format YYYY-YYYY for cohort tracking.")
            return redirect(url_for("cohort_tracking"))

        if not start_grade.isdigit() or int(start_grade) not in SUPPORTED_GRADES:
            flash("Select a valid starting grade from Grade 7 to Grade 10.")
            return redirect(url_for("cohort_tracking"))

        start_grade = int(start_grade)
        expected_path = build_expected_path(start_year, start_grade)

        conn = get_db_connection()
        cursor = conn.cursor()
        cohort_rows, summary = build_cohort_tracking(cursor, start_year, start_grade, expected_path)
        cursor.close()
        conn.close()

    return render_template(
        "co_tracking.html",
        grades=SUPPORTED_GRADES,
        start_year=start_year,
        start_grade=int(start_grade) if str(start_grade).isdigit() else 7,
        expected_path=expected_path,
        cohort_rows=cohort_rows,
        summary=summary,
    )


def get_metrics():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM students")
    total = cursor.fetchone()[0]

    cursor.close()
    conn.close()

    return total


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

    cursor.execute("SELECT COUNT(DISTINCT lrn) FROM student_records WHERE grade_level = 7")
    grade_7 = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(DISTINCT lrn) FROM student_records WHERE grade_level = 10")
    grade_10 = cursor.fetchone()[0] or 0

    cohort_survival_rate = round((grade_10 / grade_7) * 100, 2) if grade_7 else 0
    completion_rate = round((grade_10 / total_students) * 100, 2) if total_students else 0
    retention_rate = round(((total_records - (transfer_out or 0)) / total_records) * 100, 2) if total_records else 0
    repetition_rate = round((repeaters / total_records) * 100, 2) if total_records else 0

    return {
        "total_students": total_students,
        "total_records": total_records,
        "active_students": enrolled or 0,
        "transfer_in": transfer_in or 0,
        "pending_transfer_in": pending_transfer_in or 0,
        "transfer_out": transfer_out or 0,
        "repeaters": repeaters,
        "completed": grade_10,
        "cohort_survival_rate": cohort_survival_rate,
        "completion_rate": completion_rate,
        "retention_rate": retention_rate,
        "repetition_rate": repetition_rate,
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


def get_student_records(cursor, filters):
    query = """
        SELECT s.lrn, s.name, r.gender, r.grade_level, r.school_year, r.status, r.remarks
        FROM students s
        JOIN student_records r ON s.lrn = r.lrn
        WHERE 1=1
    """
    params = []

    if filters.get("q"):
        query += " AND (s.lrn LIKE %s OR s.name LIKE %s)"
        search = f"%{filters['q']}%"
        params.extend([search, search])

    if filters.get("grade") and filters["grade"].isdigit() and int(filters["grade"]) in SUPPORTED_GRADES:
        query += " AND r.grade_level = %s"
        params.append(int(filters["grade"]))

    if filters.get("status"):
        query += " AND r.status = %s"
        params.append(filters["status"])

    if filters.get("year") and SCHOOL_YEAR_PATTERN.match(filters["year"]):
        query += " AND r.school_year = %s"
        params.append(filters["year"])

    query += " ORDER BY r.school_year DESC, r.grade_level, s.name LIMIT 300"
    cursor.execute(query, params)

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


def humanize_status(status):
    labels = {
        "ENROLLED": "Enrolled",
        "TRANSFER_IN": "Transferred In",
        "PENDING_TRANSFER_IN": "Pending Transfer-In",
        "TRANSFER_OUT": "Transferred Out",
        "MISSING": "Missing",
        "REPEATED": "Repeated",
        "COMPLETED": "Completed",
        "STRAIGHT_PATH": "Straight Path",
        "INCOMPLETE": "Incomplete",
    }
    return labels.get(status, str(status or "").replace("_", " ").title())


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


def build_year_grade_calculator(cursor, base_year, compare_year, grade_from, grade_to):
    expected_grade_to = grade_from + 1

    if grade_from >= 10:
        return {
            "base_year": base_year,
            "compare_year": compare_year,
            "grade_from": grade_from,
            "grade_to": grade_to,
            "has_data": False,
            "message": "Promotion calculator only supports Grade 7 to Grade 10 progression paths.",
            "retention": {"retained": 0, "dropped": 0, "rate": 0},
            "promotion": {"promoted": 0, "repeated": 0, "dropped": 0, "rate": 0},
        }

    if grade_to != expected_grade_to:
        return {
            "base_year": base_year,
            "compare_year": compare_year,
            "grade_from": grade_from,
            "grade_to": grade_to,
            "has_data": False,
            "message": f"Invalid progression path. Grade {grade_from} should be compared to Grade {expected_grade_to}.",
            "retention": {"retained": 0, "dropped": 0, "rate": 0},
            "promotion": {"promoted": 0, "repeated": 0, "dropped": 0, "rate": 0},
        }

    cursor.execute(
        """
        SELECT COUNT(DISTINCT lrn)
        FROM student_records
        WHERE school_year = %s
        AND grade_level = %s
        """,
        (base_year, grade_from),
    )
    base_total = cursor.fetchone()[0] or 0

    if base_total == 0:
        return {
            "base_year": base_year,
            "compare_year": compare_year,
            "grade_from": grade_from,
            "grade_to": grade_to,
            "has_data": False,
            "message": f"No Grade {grade_from} records found for {base_year}.",
            "retention": {"retained": 0, "dropped": 0, "rate": 0},
            "promotion": {"promoted": 0, "repeated": 0, "dropped": 0, "rate": 0},
        }

    cursor.execute(
        """
        SELECT COUNT(DISTINCT lrn)
        FROM student_records
        WHERE school_year = %s
        AND grade_level = %s
        """,
        (compare_year, grade_to),
    )
    compare_total = cursor.fetchone()[0] or 0

    if compare_total == 0:
        return {
            "base_year": base_year,
            "compare_year": compare_year,
            "grade_from": grade_from,
            "grade_to": grade_to,
            "has_data": False,
            "message": f"No Grade {grade_to} records found for {compare_year}.",
            "retention": {"retained": 0, "dropped": 0, "rate": 0},
            "promotion": {"promoted": 0, "repeated": 0, "dropped": 0, "rate": 0},
        }

    cursor.execute(
        """
        SELECT COUNT(DISTINCT r1.lrn)
        FROM student_records r1
        JOIN student_records r2 ON r1.lrn = r2.lrn
        WHERE r1.school_year = %s
        AND r1.grade_level = %s
        AND r2.school_year = %s
        """,
        (base_year, grade_from, compare_year),
    )
    retained = cursor.fetchone()[0] or 0

    cursor.execute(
        """
        SELECT COUNT(DISTINCT r1.lrn)
        FROM student_records r1
        JOIN student_records r2 ON r1.lrn = r2.lrn
        WHERE r1.school_year = %s
        AND r1.grade_level = %s
        AND r2.school_year = %s
        AND r2.grade_level = %s
        """,
        (base_year, grade_from, compare_year, grade_to),
    )
    promoted = cursor.fetchone()[0] or 0

    cursor.execute(
        """
        SELECT COUNT(DISTINCT r1.lrn)
        FROM student_records r1
        JOIN student_records r2 ON r1.lrn = r2.lrn
        WHERE r1.school_year = %s
        AND r1.grade_level = %s
        AND r2.school_year = %s
        AND r2.grade_level = %s
        """,
        (base_year, grade_from, compare_year, grade_from),
    )
    repeated = cursor.fetchone()[0] or 0

    retention_dropped = base_total - retained
    promotion_dropped = base_total - (promoted + repeated)

    return {
        "base_year": base_year,
        "compare_year": compare_year,
        "grade_from": grade_from,
        "grade_to": grade_to,
        "has_data": True,
        "message": "",
        "retention": {
            "retained": retained,
            "dropped": retention_dropped,
            "rate": round((retained / base_total) * 100, 2),
        },
        "promotion": {
            "promoted": promoted,
            "repeated": repeated,
            "dropped": promotion_dropped,
            "rate": round((promoted / base_total) * 100, 2),
        },
    }


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


def style_report_sheet(sheet):
    dark_blue = "12376F"
    light_blue = "EAF1FF"
    border_color = "D9E2F3"
    thin = Side(style="thin", color=border_color)

    for row in sheet.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor=dark_blue)
        cell.font = Font(color="FFFFFF", bold=True, size=14)
        cell.alignment = Alignment(horizontal="center")

    for row_number in (5, 12):
        for cell in sheet[row_number]:
            cell.fill = PatternFill("solid", fgColor=light_blue)
            cell.font = Font(bold=True, color=dark_blue)

    for column_index, column_cells in enumerate(sheet.columns, start=1):
        column = get_column_letter(column_index)
        max_length = max(len(str(cell.value or "")) for cell in column_cells)
        sheet.column_dimensions[column].width = min(max(max_length + 3, 12), 42)

    sheet.freeze_panes = "A13"


def build_progression_report_workbook(
    stats,
    grade_distribution,
    records,
    school_year="",
    at_risk_students=None,
    csr_breakdown=None,
):
    at_risk_students = at_risk_students or []
    csr_breakdown = csr_breakdown or []
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Progression Report"

    report_scope = school_year if school_year else "All Uploaded School Years"
    sheet.merge_cells("A1:H1")
    sheet["A1"] = "Integrated LRN-Based Student Progression Tracking System"
    sheet["A2"] = "Report Type"
    sheet["B2"] = "Progression Summary"
    sheet["A3"] = "School Year"
    sheet["B3"] = report_scope

    summary_rows = [
        ("Metric", "Value", "", "Rate", "Value", "", "Grade", "Tracked Students"),
        ("Total Students", stats["total_students"], "", "Cohort Survival Rate", f"{stats['cohort_survival_rate']}%", "", "Grade 7", grade_distribution[0]["count"]),
        ("Total Records", stats["total_records"], "", "Completion Rate", f"{stats['completion_rate']}%", "", "Grade 8", grade_distribution[1]["count"]),
        ("Transfer-In", stats["transfer_in"], "", "Retention Rate", f"{stats['retention_rate']}%", "", "Grade 9", grade_distribution[2]["count"]),
        ("Transfer-Out", stats["transfer_out"], "", "Repetition Rate", f"{stats['repetition_rate']}%", "", "Grade 10", grade_distribution[3]["count"]),
        ("Repeaters", stats["repeaters"], "", "", "", "", "", ""),
    ]

    start_row = 5
    for offset, row in enumerate(summary_rows):
        for column, value in enumerate(row, start=1):
            sheet.cell(row=start_row + offset, column=column, value=value)

    table_start = 12
    headers = ["LRN", "Student Name", "Sex", "Grade", "School Year", "Status", "Remarks", "Tracking Note"]
    for column, header in enumerate(headers, start=1):
        sheet.cell(row=table_start, column=column, value=header)

    for offset, record in enumerate(records, start=1):
        note = "Transfer record" if "TRANSFER" in record["status"] else "Active/standard record"
        row = table_start + offset
        sheet.cell(row=row, column=1, value=record["lrn"])
        sheet.cell(row=row, column=2, value=record["name"])
        sheet.cell(row=row, column=3, value=record["gender"])
        sheet.cell(row=row, column=4, value=f"Grade {record['grade_level']}")
        sheet.cell(row=row, column=5, value=record["school_year"])
        sheet.cell(row=row, column=6, value=record["status_label"])
        sheet.cell(row=row, column=7, value=record["remarks"])
        sheet.cell(row=row, column=8, value=note)

    if not records:
        sheet.cell(row=table_start + 1, column=1, value="No records found for this report scope.")

    style_report_sheet(sheet)

    risk_sheet = workbook.create_sheet("At-Risk Students")
    risk_sheet.merge_cells("A1:F1")
    risk_sheet["A1"] = "At-Risk Students"
    risk_headers = ["LRN", "Student Name", "Last Grade Seen", "Last School Year", "Reason", "Remarks"]
    for column, header in enumerate(risk_headers, start=1):
        risk_sheet.cell(row=5, column=column, value=header)

    for row_index, student in enumerate(at_risk_students, start=6):
        risk_sheet.cell(row=row_index, column=1, value=student["lrn"])
        risk_sheet.cell(row=row_index, column=2, value=student["name"])
        risk_sheet.cell(row=row_index, column=3, value=f"Grade {student['last_grade']}")
        risk_sheet.cell(row=row_index, column=4, value=student["last_year"])
        risk_sheet.cell(row=row_index, column=5, value=student["reason"])
        risk_sheet.cell(row=row_index, column=6, value=student["remarks"])

    if not at_risk_students:
        risk_sheet.cell(row=6, column=1, value="No at-risk students found.")

    style_report_sheet(risk_sheet)

    csr_sheet = workbook.create_sheet("CSR Breakdown")
    csr_sheet.merge_cells("A1:E1")
    csr_sheet["A1"] = "Cohort Survival Leaving-Point Breakdown"
    csr_headers = [
        "Leaving Point",
        "Transferred Out",
        "Pending Transfer-In / Needs Verification",
        "Did Not Appear After Grade",
        "Total Left",
    ]
    for column, header in enumerate(csr_headers, start=1):
        csr_sheet.cell(row=5, column=column, value=header)

    for row_index, item in enumerate(csr_breakdown, start=6):
        csr_sheet.cell(row=row_index, column=1, value=f"Grade {item['grade']}")
        csr_sheet.cell(row=row_index, column=2, value=item["transferred_out"])
        csr_sheet.cell(row=row_index, column=3, value=item["pending_verification"])
        csr_sheet.cell(row=row_index, column=4, value=item["missing_after"])
        csr_sheet.cell(row=row_index, column=5, value=item["total_left"])

    style_report_sheet(csr_sheet)
    return workbook


def compute_retention(year1, year2):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM student_records r1
        JOIN student_records r2
        ON r1.lrn = r2.lrn
        WHERE r1.school_year = %s
        AND r2.school_year = %s
        """,
        (year1, year2),
    )
    retained = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(DISTINCT lrn)
        FROM student_records
        WHERE school_year = %s
        """,
        (year1,),
    )
    total = cursor.fetchone()[0]

    dropped = total - retained
    rate = (retained / total * 100) if total > 0 else 0

    cursor.close()
    conn.close()

    return {
        "retained": retained,
        "dropped": dropped,
        "rate": round(rate, 2),
    }


def compute_promotion(year1, year2, grade_from, grade_to):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM student_records r1
        JOIN student_records r2
        ON r1.lrn = r2.lrn
        WHERE r1.school_year = %s
        AND r2.school_year = %s
        AND r1.grade_level = %s
        AND r2.grade_level = %s
        """,
        (year1, year2, grade_from, grade_to),
    )
    promoted = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(DISTINCT lrn)
        FROM student_records
        WHERE school_year = %s
        AND grade_level = %s
        """,
        (year1, grade_from),
    )
    total = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM student_records r1
        JOIN student_records r2
        ON r1.lrn = r2.lrn
        WHERE r1.school_year = %s
        AND r2.school_year = %s
        AND r1.grade_level = %s
        AND r2.grade_level = %s
        """,
        (year1, year2, grade_from, grade_from),
    )
    repeated = cursor.fetchone()[0]

    dropped = total - (promoted + repeated)
    rate = (promoted / total * 100) if total > 0 else 0

    cursor.close()
    conn.close()

    return {
        "promoted": promoted,
        "repeated": repeated,
        "dropped": dropped,
        "rate": round(rate, 2),
    }


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


def summarize_cohort_status(path_cells):
    statuses = [cell["status"] for cell in path_cells if cell["status"] != "MISSING"]
    grades = [cell["actual_grade"] for cell in path_cells if cell["actual_grade"] is not None]

    if "TRANSFER_OUT" in statuses:
        return "TRANSFER_OUT"

    if "TRANSFER_IN" in statuses or "PENDING_TRANSFER_IN" in statuses:
        return "TRANSFER_IN"

    if len(grades) != len(set(grades)):
        return "REPEATED"

    if any(cell["status"] == "MISSING" for cell in path_cells):
        return "INCOMPLETE"

    if path_cells and path_cells[-1]["status"] != "MISSING" and path_cells[-1]["expected_grade"] == 10:
        return "COMPLETED"

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
        "repeated": 0,
        "transfer_in": 0,
        "transfer_out": 0,
        "dropped": 0,
        "incomplete": 0,
    }

    for lrn, name in cohort_students:
        cursor.execute(
            """
            SELECT school_year, grade_level, status, remarks
            FROM student_records
            WHERE lrn = %s
            ORDER BY school_year, grade_level
            """,
            (lrn,),
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

        result = summarize_cohort_status(path_cells)

        if result == "COMPLETED":
            summary["completed"] += 1
            summary["straight_path"] += 1
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

        rows.append(
            {
                "lrn": lrn,
                "name": name,
                "path": path_cells,
                "result": result,
                "result_label": humanize_status(result),
            }
        )

    return rows, summary


def ensure_schema():
    global SCHEMA_READY

    if SCHEMA_READY:
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    schema_updates = [
        "ALTER TABLE students ADD COLUMN gender VARCHAR(10)",
        "ALTER TABLE students ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
        "ALTER TABLE student_records ADD COLUMN remarks TEXT",
        "ALTER TABLE student_records ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
        "ALTER TABLE student_records ADD UNIQUE KEY unique_student_year_grade (lrn, school_year, grade_level)",
        "ALTER TABLE student_change_logs ADD COLUMN changed_by VARCHAR(80)",
    ]

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS student_change_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            lrn VARCHAR(20),
            field_name VARCHAR(100),
            old_value TEXT,
            new_value TEXT,
            school_year VARCHAR(20),
            grade_level INT,
            changed_by VARCHAR(80),
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lrn) REFERENCES students(lrn)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(80) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(50) NOT NULL DEFAULT 'admin',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    for statement in schema_updates:
        try:
            cursor.execute(statement)
        except mysql.connector.Error as error:
            if error.errno not in (1060, 1061, 1062):
                raise

    cursor.execute(
        """
        UPDATE student_records
        SET remarks = ''
        WHERE LOWER(TRIM(remarks)) = 'nan'
        """
    )

    cursor.execute(
        """
        UPDATE student_records
        SET status = CASE
            WHEN UPPER(COALESCE(remarks, '')) LIKE '%PENDING TI%'
                OR UPPER(COALESCE(remarks, '')) LIKE '%PENDING T/I%'
                THEN 'PENDING_TRANSFER_IN'
            WHEN UPPER(COALESCE(remarks, '')) LIKE '%T/O%'
                OR UPPER(COALESCE(remarks, '')) LIKE '%T-O%'
                OR UPPER(COALESCE(remarks, '')) LIKE '%TRANSFER OUT%'
                OR UPPER(COALESCE(remarks, '')) LIKE '%TRANSFERRED OUT%'
                OR UPPER(COALESCE(remarks, '')) LIKE '%TRANSFER-OUT%'
                THEN 'TRANSFER_OUT'
            WHEN UPPER(COALESCE(remarks, '')) LIKE '%T/I%'
                OR UPPER(COALESCE(remarks, '')) LIKE '%T-I%'
                OR UPPER(COALESCE(remarks, '')) LIKE '%TRANSFER IN%'
                OR UPPER(COALESCE(remarks, '')) LIKE '%TRANSFERRED IN%'
                OR UPPER(COALESCE(remarks, '')) LIKE '%TRANSFER-IN%'
                THEN 'TRANSFER_IN'
            ELSE 'ENROLLED'
        END
        """
    )

    cursor.execute("SELECT COUNT(*) FROM users")
    user_count = cursor.fetchone()[0] or 0
    if user_count == 0:
        cursor.execute(
            """
            INSERT INTO users (username, password_hash, role)
            VALUES (%s, %s, %s)
            """,
            ("admin", generate_password_hash("admin123"), "admin"),
        )

    conn.commit()
    cursor.close()
    conn.close()
    SCHEMA_READY = True


def normalize_gender(value):
    gender = str(value).strip().upper()

    if gender.startswith("M"):
        return "MALE"
    if gender.startswith("F"):
        return "FEMALE"
    return "UNKNOWN"


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


def normalize_remarks(value):
    remarks = str(value).strip()

    if remarks.lower() == "nan":
        return ""

    return remarks


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

    if log_change(cursor, lrn, "student.name", old_name, name, school_year, grade_level, changed_by):
        changes_logged += 1
    if log_change(cursor, lrn, "student.gender", old_gender, gender, school_year, grade_level, changed_by):
        changes_logged += 1

    cursor.execute(
        """
        UPDATE students
        SET name = %s, gender = %s
        WHERE lrn = %s
        """,
        (name, gender, lrn),
    )
    return changes_logged


def upsert_student_record(cursor, lrn, school_year, grade_level, gender, status, remarks, changed_by=None):
    cursor.execute(
        """
        SELECT id, gender, status, remarks
        FROM student_records
        WHERE lrn = %s
        AND school_year = %s
        AND grade_level = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (lrn, school_year, grade_level),
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

    if log_change(cursor, lrn, "record.gender", old_gender, gender, school_year, grade_level, changed_by):
        changes_logged += 1
    if log_change(cursor, lrn, "record.status", old_status, status, school_year, grade_level, changed_by):
        changes_logged += 1
    if log_change(cursor, lrn, "record.remarks", old_remarks, remarks, school_year, grade_level, changed_by):
        changes_logged += 1

    cursor.execute(
        """
        UPDATE student_records
        SET gender = %s, status = %s, remarks = %s
        WHERE id = %s
        """,
        (gender, status, remarks, record_id),
    )
    return "updated", changes_logged


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

    columns["data_start_row"] = header_row + 2
    return columns


@app.route("/upload", methods=["POST"])
@admin_required
def upload():
    try:
        ensure_schema()

        file = request.files["file"]
        school_year = request.form.get("school_year")
        grade_level = request.form.get("grade_level")

        if not file:
            return "No file uploaded"

        if not school_year or not SCHOOL_YEAR_PATTERN.match(school_year):
            return "Invalid school year. Use format YYYY-YYYY, for example 2026-2027."

        if not grade_level or not grade_level.isdigit() or int(grade_level) not in SUPPORTED_GRADES:
            return "Invalid grade level. Only Grades 7 to 10 are supported."

        grade_level = int(grade_level)
        df = pd.read_excel(file, header=None)

        columns = find_sf1_columns(df)

        print("\n===== DATA SAMPLE =====")
        print(df.head())

        if columns is None:
            return "Column detection failed. Check Excel format."

        print("LRN COL:", columns["lrn"])
        print("NAME COL:", columns["name"])
        print("SEX COL:", columns["sex"])

        selected_columns = {
            columns["lrn"]: "LRN",
            columns["name"]: "NAME",
            columns["sex"]: "SEX",
        }

        if "remarks" in columns:
            selected_columns[columns["remarks"]] = "REMARKS"

        df = df.iloc[columns["data_start_row"]:]
        df = df[list(selected_columns.keys())].rename(columns=selected_columns)

        if "REMARKS" not in df.columns:
            df["REMARKS"] = ""

        df = df.dropna(subset=["LRN"])
        df["LRN"] = df["LRN"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        df = df[df["LRN"].str.match(LRN_PATTERN)]
        df = df[df["NAME"].notna()]
        df = df[df["NAME"].astype(str).str.strip() != ""]

        print("\n===== CLEAN DATA =====")
        print(df.head(10))

        conn = get_db_connection()
        cursor = conn.cursor()

        records_inserted = 0
        records_updated = 0
        changes_logged = 0
        changed_by = session.get("username")

        for _, row in df.iterrows():
            lrn = str(row["LRN"]).strip()
            name = str(row["NAME"]).strip()
            gender = normalize_gender(row["SEX"])
            remarks = normalize_remarks(row["REMARKS"])
            status = detect_status_from_remarks(remarks)

            print("INSERTING:", lrn, name, gender)

            changes_logged += upsert_student(cursor, lrn, name, gender, school_year, grade_level, changed_by)
            record_action, record_changes = upsert_student_record(
                cursor,
                lrn,
                school_year,
                grade_level,
                gender,
                status,
                remarks,
                changed_by,
            )
            changes_logged += record_changes

            if record_action == "inserted":
                records_inserted += 1
            else:
                records_updated += 1

        conn.commit()
        cursor.close()
        conn.close()

        flash(
            f"{records_inserted} records imported, "
            f"{records_updated} records updated, "
            f"{changes_logged} changes logged."
        )
        return redirect(url_for("lis_upload"))

    except Exception as e:
        print("\nERROR:", str(e))
        return f"Error occurred: {str(e)}"


def get_db_connection():
    return mysql.connector.connect(
        host="db",
        user="root",
        password="root",
        database="mydb",
    )


def get_school_years_for_computation():
    conn = get_db_connection()
    cursor = conn.cursor()
    years = get_school_years(cursor)
    cursor.close()
    conn.close()
    return years

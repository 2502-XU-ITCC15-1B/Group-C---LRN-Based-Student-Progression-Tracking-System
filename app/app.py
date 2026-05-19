from flask import Flask, flash, redirect, render_template, request, send_file, send_from_directory, session, url_for
from datetime import timedelta
from functools import wraps
from io import BytesIO
import os
import re
import secrets
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
from backend.constants import (
    EDITABLE_RECORD_STATUSES,
    LRN_PATTERN,
    MAX_UPLOAD_SIZE_MB,
    PER_PAGE,
    SUPPORTED_GRADES,
)
from backend.cohort_service import build_cohort_tracking, build_expected_path, build_grade7_cohort_report
from backend.dashboard_service import get_dashboard_stats, get_grade_distribution, get_recent_changes
from backend.db import ensure_schema, get_db_connection
from backend.formatters import (
    detect_status_from_remarks,
    format_remarks,
    humanize_status,
    normalize_gender,
    normalize_remarks,
    status_badge_class,
)
from backend.report_service import build_grade7_cohort_report_workbook
from backend.student_service import (
    count_student_records,
    get_student_records,
    log_change,
    upsert_student,
    upsert_student_record,
)
from backend.upload_service import (
    StoredUpload,
    detect_upload_metadata,
    find_sf1_columns,
    get_upload_metadata_mismatches,
    pop_pending_upload,
    read_lis_upload,
    save_pending_upload,
)
from backend.validators import is_valid_school_year

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=20)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE_MB * 1024 * 1024


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(error):
    flash(f"Uploaded file is too large. Maximum file size is {MAX_UPLOAD_SIZE_MB} MB.", "upload_error")
    return redirect(url_for("lis_upload"))


@app.before_request
def refresh_logged_in_session():
    if "user_id" in session:
        session.permanent = True
        session.modified = True

        ensure_schema()
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT username, role, is_active
            FROM users
            WHERE id = %s
            """,
            (session["user_id"],),
        )
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if not user or not user["is_active"]:
            session.clear()
            flash("Your account is no longer active. Please contact the administrator.")
            return redirect(url_for("login"))

        session["username"] = user["username"]
        session["role"] = user["role"]


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
    return {
        "current_user": session.get("username"),
        "current_role": session.get("role"),
        "status_badge_class": status_badge_class,
    }


def _legacy_detect_upload_metadata(df):
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


@app.route("/")
def root():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


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
        session.permanent = True
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["role"] = user["role"]
        return redirect(url_for("dashboard"))

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


@app.route("/forgot-password")
def forgot_password():
    return render_template("forgot_password.html")


@app.route("/users", methods=["GET", "POST"])
@admin_required
def manage_users():
    ensure_schema()

    allowed_roles = {"admin", "viewer"}

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        user_id = request.form.get("user_id", "").strip()

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        if action == "add":
            username = request.form.get("username", "").strip()
            role = request.form.get("role", "viewer").strip()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not username:
                flash("Username is required.")
            elif role not in allowed_roles:
                flash("Invalid user role.")
            elif len(password) < 8:
                flash("Password must be at least 8 characters.")
            elif password != confirm_password:
                flash("Password and confirmation do not match.")
            else:
                try:
                    cursor.execute(
                        """
                        INSERT INTO users (username, password_hash, role, is_active)
                        VALUES (%s, %s, %s, TRUE)
                        """,
                        (username, generate_password_hash(password), role),
                    )
                    conn.commit()
                    flash("User account added successfully.")
                except Exception:
                    conn.rollback()
                    flash("Username already exists or could not be added.")

        elif action == "update":
            role = request.form.get("role", "viewer").strip()
            is_active = "1" in request.form.getlist("is_active")

            if role not in allowed_roles:
                flash("Invalid user role.")
            elif user_id == str(session.get("user_id")) and not is_active:
                flash("You cannot deactivate your own account.")
            else:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS active_admins
                    FROM users
                    WHERE role = 'admin'
                        AND is_active = TRUE
                        AND id <> %s
                    """,
                    (user_id,),
                )
                active_admins = cursor.fetchone()["active_admins"] or 0
                would_remove_admin_access = role != "admin" or not is_active

                if would_remove_admin_access and active_admins == 0:
                    flash("At least one active admin account is required.")
                else:
                    cursor.execute(
                        """
                        UPDATE users
                        SET role = %s, is_active = %s
                        WHERE id = %s
                        """,
                        (role, is_active, user_id),
                    )
                    conn.commit()
                    if user_id == str(session.get("user_id")):
                        session["role"] = role
                    flash("User account updated successfully.")

        elif action == "reset_password":
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            if user_id == str(session.get("user_id")):
                flash("Use Change Password to update your own password.")
            elif len(new_password) < 8:
                flash("Temporary password must be at least 8 characters.")
            elif new_password != confirm_password:
                flash("Temporary password and confirmation do not match.")
            else:
                cursor.execute(
                    """
                    UPDATE users
                    SET password_hash = %s
                    WHERE id = %s
                    """,
                    (generate_password_hash(new_password), user_id),
                )
                conn.commit()
                flash("Password reset successfully. Ask the user to change it after logging in.")

        elif action == "delete":
            if user_id == str(session.get("user_id")):
                flash("You cannot delete your own account.")
            else:
                cursor.execute(
                    """
                    SELECT id, username, role, is_active
                    FROM users
                    WHERE id = %s
                    """,
                    (user_id,),
                )
                target_user = cursor.fetchone()

                if not target_user:
                    flash("User account was not found.")
                else:
                    cursor.execute(
                        """
                        SELECT COUNT(*) AS active_admins
                        FROM users
                        WHERE role = 'admin'
                            AND is_active = TRUE
                            AND id <> %s
                        """,
                        (user_id,),
                    )
                    active_admins = cursor.fetchone()["active_admins"] or 0

                    if target_user["role"] == "admin" and target_user["is_active"] and active_admins == 0:
                        flash("At least one active admin account is required.")
                    else:
                        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
                        conn.commit()
                        flash("User account deleted successfully.")

        cursor.close()
        conn.close()
        return redirect(url_for("manage_users"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT id, username, role, is_active, created_at
        FROM users
        ORDER BY created_at DESC, username ASC
        """
    )
    users = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template("users.html", users=users)


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
@admin_required
def lis_upload():
    cancel_upload = request.args.get("cancel_upload", "").strip()
    if cancel_upload:
        pending = pop_pending_upload(cancel_upload)
        if pending:
            try:
                os.remove(pending["path"])
            except OSError:
                pass
        return redirect(url_for("lis_upload"))

    return render_template("lis_upload.html", grades=SUPPORTED_GRADES)


@app.route("/records/delete-batch", methods=["POST"])
@admin_required
def delete_batch_records():
    ensure_schema()

    school_year = request.form.get("school_year", "").strip()
    grade_level = request.form.get("grade_level", "").strip()
    confirmation = request.form.get("confirmation", "").strip()

    if not is_valid_school_year(school_year):
        flash("Use school year format YYYY-YYYY with consecutive years before deleting a batch.")
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
        "page": request.args.get("page", "1").strip(),
    }
    page = int(filters["page"]) if filters["page"].isdigit() and int(filters["page"]) > 0 else 1

    conn = get_db_connection()
    cursor = conn.cursor()
    total_matching = count_student_records(cursor, filters)
    total_pages = max(1, (total_matching + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)
    filters["page"] = str(page)
    records = get_student_records(cursor, filters, page, PER_PAGE)
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
        page=page,
        per_page=PER_PAGE,
        total_matching=total_matching,
        total_pages=total_pages,
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
    confirmation = request.form.get("confirmation", "").strip()

    if confirmation != "ADD STUDENT":
        flash("Type ADD STUDENT to confirm manual student creation.")
        return redirect(url_for("students"))

    if not LRN_PATTERN.match(lrn):
        flash("Enter a valid 12-digit LRN before adding a student.")
        return redirect(url_for("students"))

    if not name:
        flash("Student name is required.")
        return redirect(url_for("students"))

    if gender not in {"MALE", "FEMALE"}:
        flash("Select a valid sex value.")
        return redirect(url_for("students"))

    if not is_valid_school_year(school_year):
        flash("Use school year format YYYY-YYYY with consecutive years before adding a student.")
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
        SELECT name
        FROM students
        WHERE lrn = %s
        LIMIT 1
        """,
        (lrn,),
    )
    existing_student = cursor.fetchone()

    if existing_student:
        cursor.close()
        conn.close()
        flash(f"Duplicate LRN rejected. {lrn} already belongs to {existing_student[0]}.")
        return redirect(url_for("students"))

    cursor.execute(
        """
        SELECT id
        FROM student_records
        WHERE lrn = %s
        AND school_year = %s
        LIMIT 1
        """,
        (lrn, school_year),
    )

    if cursor.fetchone():
        cursor.close()
        conn.close()
        flash(f"Duplicate school year rejected. {lrn} already has a record in {school_year}.")
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


@app.route("/student/<lrn>/record/add", methods=["POST"])
@admin_required
def add_student_year_record(lrn):
    ensure_schema()

    if not LRN_PATTERN.match(lrn):
        flash("Invalid LRN. Use exactly 12 digits.")
        return redirect(url_for("students"))

    gender = normalize_gender(request.form.get("gender", ""))
    school_year = request.form.get("school_year", "").strip()
    grade_level = request.form.get("grade_level", "").strip()
    status = request.form.get("status", "").strip()
    remarks = normalize_remarks(request.form.get("remarks", ""))
    confirmation = request.form.get("confirmation", "").strip()

    if confirmation != "ADD RECORD":
        flash("Type ADD RECORD to confirm adding a new school year record.")
        return redirect(url_for("student_history", lrn=lrn))

    if gender not in {"MALE", "FEMALE"}:
        flash("Select a valid sex value.")
        return redirect(url_for("student_history", lrn=lrn))

    if not is_valid_school_year(school_year):
        flash("Use school year format YYYY-YYYY with consecutive years before adding a year record.")
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
    cursor.execute("SELECT name FROM students WHERE lrn = %s", (lrn,))
    student = cursor.fetchone()

    if student is None:
        cursor.close()
        conn.close()
        flash("No student found for that LRN.")
        return redirect(url_for("students"))

    cursor.execute(
        """
        SELECT id, grade_level
        FROM student_records
        WHERE lrn = %s
        AND school_year = %s
        LIMIT 1
        """,
        (lrn, school_year),
    )
    existing = cursor.fetchone()

    if existing:
        cursor.close()
        conn.close()
        flash(f"Duplicate school year rejected. {lrn} already has a Grade {existing[1]} record in {school_year}.")
        return redirect(url_for("student_history", lrn=lrn))

    changed_by = session.get("username")
    cursor.execute(
        """
        INSERT INTO student_records (lrn, school_year, grade_level, gender, status, remarks)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (lrn, school_year, grade_level, gender, status, remarks),
    )
    log_change(cursor, lrn, "manual.student_record", "", f"Added Grade {grade_level} record for {school_year}", school_year, grade_level, changed_by)
    conn.commit()
    cursor.close()
    conn.close()

    flash(f"Added Grade {grade_level} record for {student[0]} in {school_year}.")
    return redirect(url_for("student_history", lrn=lrn))


@app.route("/reports")
@login_required
def reports():
    start_year = request.args.get("start_year", "").strip()
    start_grade = request.args.get("start_grade", "7").strip()
    if start_year:
        return redirect(url_for("cohort_tracking", start_year=start_year, start_grade=start_grade))
    return redirect(url_for("cohort_tracking"))


@app.route("/reports/export")
@login_required
def export_report():
    ensure_schema()

    start_year = request.args.get("start_year", "").strip()
    start_grade = request.args.get("start_grade", "7").strip()
    if not start_year:
        flash("Select a cohort starting school year before exporting.")
        return redirect(url_for("cohort_tracking"))
    if not is_valid_school_year(start_year):
        flash("Use a valid starting school year in YYYY-YYYY format before exporting.")
        return redirect(url_for("cohort_tracking"))
    if not start_grade.isdigit() or int(start_grade) not in SUPPORTED_GRADES:
        flash("Select a valid Grade 7 to Grade 10 entry level before exporting.")
        return redirect(url_for("cohort_tracking", start_year=start_year))

    start_grade = int(start_grade)

    conn = get_db_connection()
    cursor = conn.cursor()
    report = build_grade7_cohort_report(cursor, start_year, start_grade)
    cursor.close()
    conn.close()

    workbook = build_grade7_cohort_report_workbook(report)
    output = BytesIO()
    workbook.save(output)
    output.seek(0)

    filename = f"grade-{start_grade}-entry-cohort-progression-report-{start_year}.xlsx"

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

    start_year = request.args.get("start_year", "").strip()
    start_grade = request.args.get("start_grade", "7").strip()
    if not start_year:
        flash("Select a cohort starting school year before printing.")
        return redirect(url_for("cohort_tracking"))
    if not is_valid_school_year(start_year):
        flash("Use a valid starting school year in YYYY-YYYY format before printing.")
        return redirect(url_for("cohort_tracking"))
    if not start_grade.isdigit() or int(start_grade) not in SUPPORTED_GRADES:
        flash("Select a valid Grade 7 to Grade 10 entry level before printing.")
        return redirect(url_for("cohort_tracking", start_year=start_year))

    start_grade = int(start_grade)

    conn = get_db_connection()
    cursor = conn.cursor()
    report = build_grade7_cohort_report(cursor, start_year, start_grade)
    cursor.close()
    conn.close()

    return render_template(
        "print_report.html",
        report=report,
    )


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
        grades=SUPPORTED_GRADES,
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

    if not is_valid_school_year(school_year):
        flash("Use school year format YYYY-YYYY with consecutive years before updating a record.")
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


@app.route("/cohort-tracking")
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
        "delayed": 0,
        "repeated": 0,
        "transfer_in": 0,
        "transfer_out": 0,
        "dropped": 0,
        "incomplete": 0,
        "for_review": 0,
    }
    expected_path = []

    if start_year:
        if not is_valid_school_year(start_year):
            flash("Use school year format YYYY-YYYY with consecutive years for cohort tracking.")
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


@app.route("/upload", methods=["POST"])
@admin_required
def upload():
    pending_file = None
    pending_path = None
    try:
        ensure_schema()

        confirm_mismatch = request.form.get("confirm_metadata_mismatch") == "1"
        confirm_upload_warnings = request.form.get("confirm_upload_warnings") == "1"
        pending_token = request.form.get("pending_upload_token", "").strip()
        pending = None
        file = None

        if confirm_mismatch and pending_token:
            pending = pop_pending_upload(pending_token)
            if not pending:
                flash("The pending upload expired. Please select the file and upload again.", "upload_error")
                return redirect(url_for("lis_upload"))

            pending_path = pending["path"]
            pending_file = StoredUpload(pending_path, pending["filename"])
            file = pending_file
        else:
            file = request.files.get("file")

        school_year = request.form.get("school_year", "").strip()
        grade_level = request.form.get("grade_level")

        if not file or not file.filename:
            flash("No file uploaded.", "upload_error")
            return redirect(url_for("lis_upload"))

        if not is_valid_school_year(school_year):
            flash("Invalid school year. Use consecutive format YYYY-YYYY, for example 2025-2026.", "upload_error")
            return redirect(url_for("lis_upload"))

        if not grade_level or not grade_level.isdigit() or int(grade_level) not in SUPPORTED_GRADES:
            flash("Invalid grade level. Only Grades 7 to 10 are supported.", "upload_error")
            return redirect(url_for("lis_upload"))

        grade_level = int(grade_level)

        try:
            df = read_lis_upload(file)
        except ValueError as e:
            flash(str(e), "upload_error")
            return redirect(url_for("lis_upload"))

        detected_metadata = detect_upload_metadata(df)
        metadata_mismatches = get_upload_metadata_mismatches(detected_metadata, school_year, grade_level)
        if metadata_mismatches and not confirm_mismatch:
            pending_token = save_pending_upload(file)
            return render_template(
                "lis_upload.html",
                grades=SUPPORTED_GRADES,
                pending_upload={
                    "token": pending_token,
                    "filename": file.filename,
                    "school_year": school_year,
                    "grade_level": grade_level,
                    "mismatches": metadata_mismatches,
                },
            )

        columns = find_sf1_columns(df)

        print("\n===== DATA SAMPLE =====")
        print(df.head())

        if columns is None:
            flash("Column detection failed. Check the LIS/SF1 file and make sure LRN, Name, and Sex columns are present.", "upload_error")
            return redirect(url_for("lis_upload"))

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

        raw_row_count = len(df)
        lrn_values = df["LRN"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        name_values = df["NAME"].astype(str).str.strip()
        sex_values = df["SEX"].astype(str).str.strip()
        missing_lrn_mask = df["LRN"].isna() | (lrn_values == "") | (lrn_values.str.upper() == "NAN")
        missing_name_mask = df["NAME"].isna() | (name_values == "") | (name_values.str.upper() == "NAN")
        missing_sex_mask = df["SEX"].isna() | (sex_values == "") | (sex_values.str.upper() == "NAN")
        invalid_lrn_mask = ~lrn_values.str.match(LRN_PATTERN, na=False) & ~missing_lrn_mask
        missing_data_summary = {
            "total_rows": raw_row_count,
            "missing_lrn": int(missing_lrn_mask.sum()),
            "missing_name": int(missing_name_mask.sum()),
            "missing_sex": int(missing_sex_mask.sum()),
            "invalid_lrn": int(invalid_lrn_mask.sum()),
        }
        missing_data_summary["affected_rows"] = int(
            (missing_lrn_mask | missing_name_mask | missing_sex_mask | invalid_lrn_mask).sum()
        )
        valid_lrn_values = lrn_values[~missing_lrn_mask & ~invalid_lrn_mask]
        duplicate_lrns = sorted(valid_lrn_values[valid_lrn_values.duplicated()].unique().tolist())

        if duplicate_lrns:
            shown_duplicates = ", ".join(duplicate_lrns[:5])
            extra_count = len(duplicate_lrns) - 5
            if extra_count > 0:
                shown_duplicates = f"{shown_duplicates}, and {extra_count} more"
            flash(
                f"Duplicate LRN found in the uploaded file: {shown_duplicates}. "
                "Please remove duplicate LRNs before importing.",
                "upload_error",
            )
            return redirect(url_for("lis_upload"))

        if missing_data_summary["affected_rows"] and not confirm_upload_warnings:
            pending_token = save_pending_upload(file)
            return render_template(
                "lis_upload.html",
                grades=SUPPORTED_GRADES,
                pending_upload={
                    "token": pending_token,
                    "filename": file.filename,
                    "school_year": school_year,
                    "grade_level": grade_level,
                    "mismatches": [],
                    "missing_data": missing_data_summary,
                },
            )

        df = df.dropna(subset=["LRN", "NAME", "SEX"])
        df["LRN"] = df["LRN"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        df = df[df["LRN"].str.match(LRN_PATTERN)]
        df = df[df["NAME"].notna()]
        df = df[df["NAME"].astype(str).str.strip() != ""]
        df = df[df["SEX"].notna()]
        df = df[df["SEX"].astype(str).str.strip() != ""]
        skipped_rows = missing_data_summary["affected_rows"]

        if df.empty:
            flash("No valid student rows found. Check that the file contains 12-digit LRNs and student names.", "upload_error")
            return redirect(url_for("lis_upload"))

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
            f"{changes_logged} changes logged, "
            f"{skipped_rows} invalid/incomplete row(s) skipped."
        )
        return redirect(url_for("lis_upload"))

    except Exception as e:
        print("\nERROR:", str(e))
        flash(f"Upload failed: {str(e)}", "upload_error")
        return redirect(url_for("lis_upload"))
    finally:
        if pending_file:
            pending_file.close()
        if pending_path:
            try:
                os.remove(pending_path)
            except OSError:
                pass


from flask import Flask, render_template, request
import pandas as pd
import mysql.connector
from flask import request
from flask import redirect, url_for, flash
app = Flask(__name__)
app.secret_key = "secret123"

from flask import request

@app.route('/')
def home():
    conn = get_db_connection()
    cursor = conn.cursor()

    grade = request.args.get('grade')
    year = request.args.get('year')

    base_query = """
        FROM students s
        JOIN student_records r ON s.lrn = r.lrn
        WHERE 1=1
    """

    params = []

    if grade:
        base_query += " AND r.grade_level = %s"
        params.append(grade)

    if year:
        base_query += " AND r.school_year = %s"
        params.append(year)

    cursor.execute("SELECT s.lrn, s.name, r.gender " + base_query, params)
    students = cursor.fetchall()

    cursor.execute("""
        SELECT 
            COUNT(*),
            SUM(CASE WHEN UPPER(r.gender) LIKE 'M%' THEN 1 ELSE 0 END),
            SUM(CASE WHEN UPPER(r.gender) LIKE 'F%' THEN 1 ELSE 0 END)
    """ + base_query, params)

    result = cursor.fetchone()

    total = result[0]
    male = result[1] or 0
    female = result[2] or 0

    male_pct = round((male / total) * 100, 2) if total > 0 else 0
    female_pct = round((female / total) * 100, 2) if total > 0 else 0

    cursor.close()
    conn.close()

# 👇 ADD THIS
    try:
        promotion = compute_promotion("2024-2025", "2025-2026", 9, 10)
    except:
        promotion = {"rate": 0, "promoted": 0, "repeated": 0, "dropped": 0}

    return render_template(
    "index.html",
    students=students,
    total=total,
    male=male,
    female=female,
    male_pct=male_pct,
    female_pct=female_pct,
    promotion=promotion   # 👈 ADD THIS
    )
    

def get_metrics():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM students")
    total = cursor.fetchone()[0]

    cursor.close()
    conn.close()

    return total

def compute_promotion(year1, year2, grade_from, grade_to):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Promoted students
    cursor.execute("""
        SELECT COUNT(*)
        FROM student_records r1
        JOIN student_records r2
        ON r1.lrn = r2.lrn
        WHERE r1.school_year = %s
        AND r2.school_year = %s
        AND r1.grade_level = %s
        AND r2.grade_level = %s
    """, (year1, year2, grade_from, grade_to))

    promoted = cursor.fetchone()[0]

    # Total students in base grade
    cursor.execute("""
        SELECT COUNT(DISTINCT lrn)
        FROM student_records
        WHERE school_year = %s
        AND grade_level = %s
    """, (year1, grade_from))

    total = cursor.fetchone()[0]

    # Repeated (same grade again)
    cursor.execute("""
        SELECT COUNT(*)
        FROM student_records r1
        JOIN student_records r2
        ON r1.lrn = r2.lrn
        WHERE r1.school_year = %s
        AND r2.school_year = %s
        AND r1.grade_level = %s
        AND r2.grade_level = %s
    """, (year1, year2, grade_from, grade_from))

    repeated = cursor.fetchone()[0]

    dropped = total - (promoted + repeated)

    promotion_rate = (promoted / total * 100) if total > 0 else 0

    cursor.close()
    conn.close()

    return {
        "promoted": promoted,
        "repeated": repeated,
        "dropped": dropped,
        "rate": round(promotion_rate, 2)
    }

@app.route('/upload', methods=['POST'])
def upload():
    try:
        file = request.files['file']

        school_year = request.form.get('school_year')
        grade_level = request.form.get('grade_level')

        if not file:
            return "No file uploaded"

        df = pd.read_excel(file, header=None)

        df = df.iloc[6:]

        df = df.dropna(axis=1, how='all')

        df.columns = range(df.shape[1])

        print("\n===== DATA SAMPLE =====")
        print(df.head())

        lrn_col = None
        name_col = None
        sex_col = None

        for col in df.columns:
            col_data = df[col].astype(str)

            if col_data.str.match(r'^\d{10,}$').any():
                lrn_col = col

            if col_data.str.contains(',').any():
                name_col = col

            if col_data.str.upper().isin(['M', 'F']).any():
                sex_col = col

        print("LRN COL:", lrn_col)
        print("NAME COL:", name_col)
        print("SEX COL:", sex_col)

        if lrn_col is None or name_col is None or sex_col is None:
            return "Column detection failed. Check Excel format."

        df = df.rename(columns={
            lrn_col: 'LRN',
            name_col: 'NAME',
            sex_col: 'SEX'
        })

        df = df[['LRN', 'NAME', 'SEX']]

        # Clean data
        df = df.dropna(subset=['LRN'])
        df = df[df['LRN'].astype(str).str.isnumeric()]
        df = df[df['NAME'].notna()]

        print("\n===== CLEAN DATA =====")
        print(df.head(10))

        conn = get_db_connection()
        cursor = conn.cursor()

        inserted = 0

        for _, row in df.iterrows():
            lrn = str(row['LRN']).strip()
            name = str(row['NAME']).strip()
            gender = str(row['SEX']).strip().upper()

            # Normalize gender
            if gender.startswith('M'):
                gender = 'MALE'
            elif gender.startswith('F'):
                gender = 'FEMALE'
            else:
                gender = 'UNKNOWN'

            print("INSERTING:", lrn, name, gender)

            cursor.execute("""
                INSERT IGNORE INTO students (lrn, name)
                VALUES (%s, %s)
            """, (lrn, name))

            cursor.execute("""
            INSERT INTO student_records (lrn, school_year, grade_level, gender, status)
            VALUES (%s, %s, %s, %s, %s)
            """, (lrn, school_year, grade_level, gender, "ENROLLED"))

        conn.commit()
        cursor.close()
        conn.close()

        flash(f"{inserted} students imported successfully!")
        return redirect(url_for('home'))

    except Exception as e:
        print("\nERROR:", str(e))
        return f"Error occurred: {str(e)}"

def get_db_connection():
    return mysql.connector.connect(
        host="db",   
        user="root",
        password="root",
        database="mydb"
    )

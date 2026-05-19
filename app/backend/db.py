import os

import mysql.connector
from werkzeug.security import generate_password_hash

SCHEMA_READY = False


def get_db_connection():
    config = {
        "host": os.environ.get("DB_HOST", "db"),
        "port": int(os.environ.get("DB_PORT", "3306")),
        "user": os.environ.get("DB_USER", "root"),
        "password": os.environ.get("DB_PASSWORD", "root"),
        "database": os.environ.get("DB_NAME", "mydb"),
    }

    ssl_disabled = os.environ.get("DB_SSL_DISABLED", "").lower() in {"1", "true", "yes"}
    ssl_ca = os.environ.get("DB_SSL_CA")
    if not ssl_disabled and config["host"] != "db":
        config["ssl_disabled"] = False
        if ssl_ca:
            config["ssl_ca"] = ssl_ca

    return mysql.connector.connect(**config)


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
        "ALTER TABLE student_records ADD INDEX idx_student_records_lrn (lrn)",
        "ALTER TABLE student_records DROP INDEX unique_student_year_grade",
        "ALTER TABLE student_records ADD UNIQUE KEY unique_student_year (lrn, school_year)",
        "ALTER TABLE student_change_logs ADD COLUMN changed_by VARCHAR(80)",
    ]

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS students (
            lrn VARCHAR(20) PRIMARY KEY,
            name VARCHAR(255),
            gender VARCHAR(10),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS student_records (
            id INT AUTO_INCREMENT PRIMARY KEY,
            lrn VARCHAR(20),
            school_year VARCHAR(20),
            grade_level INT,
            gender VARCHAR(10),
            status VARCHAR(50),
            remarks TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY unique_student_year (lrn, school_year),
            INDEX idx_student_records_lrn (lrn),
            FOREIGN KEY (lrn) REFERENCES students(lrn)
        )
        """
    )

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
        DELETE r1
        FROM student_records r1
        JOIN student_records r2
            ON r1.lrn = r2.lrn
            AND r1.school_year = r2.school_year
            AND r1.id < r2.id
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
            if error.errno not in (1060, 1061, 1062, 1091):
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

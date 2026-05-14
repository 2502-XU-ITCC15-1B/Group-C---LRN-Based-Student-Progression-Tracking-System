USE mydb;

CREATE TABLE students (
    lrn VARCHAR(20) PRIMARY KEY,
    name VARCHAR(255),
    gender VARCHAR(10),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE student_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    lrn VARCHAR(20),
    school_year VARCHAR(20),
    grade_level INT,
    gender VARCHAR(10),
    status VARCHAR(50),
    remarks TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_student_year_grade (lrn, school_year, grade_level),
    FOREIGN KEY (lrn) REFERENCES students(lrn)
);

CREATE TABLE student_change_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    lrn VARCHAR(20),
    field_name VARCHAR(100),
    old_value TEXT,
    new_value TEXT,
    school_year VARCHAR(20),
    grade_level INT,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lrn) REFERENCES students(lrn)
);

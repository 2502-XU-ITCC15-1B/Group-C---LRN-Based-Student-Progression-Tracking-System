# Integrated LRN-Based Student Progression Tracking System

A Dockerized Flask web application for tracking junior high school student progression from Grade 7 to Grade 10 using the 12-digit Learner Reference Number (LRN) as the main identifier.

The system imports LIS/SF1 Excel exports, centralizes student records, tracks cohorts across school years, monitors transfer movement, computes progression indicators, and generates formatted reports for administrative review.

## Project Scope

This system is intended for school-level use by the department head or authorized school personnel. It supports:

- Uploading and processing LIS/SF1 Excel exports
- Centralized LRN-based student record management
- Grade 7 to Grade 10 progression tracking
- Transfer-in, pending transfer-in, and transfer-out monitoring
- At-risk student identification
- Cohort survival, completion, retention, repetition, and promotion calculations
- Excel report generation
- Print-friendly reports for PDF saving
- Login, logout, password hashing, access control, and password change

## Limitations

The system does not:

- Connect directly to the national LIS database API
- Replace the official LIS system
- Support elementary or senior high school records
- Support online enrollment
- Provide student or parent portals
- Handle grading or attendance monitoring

It only processes uploaded LIS/SF1 files provided by authorized school personnel.

## Tech Stack

- Python 3.11
- Flask
- Gunicorn
- MySQL 8.0
- Nginx
- Pandas
- OpenPyXL
- xlrd
- Bootstrap 5
- Docker Compose

## Main Features

### Authentication

- Login and logout
- Logout confirmation page
- Password hashing using Werkzeug
- Change password page
- Session-based route protection
- Admin-only actions for upload and deletion

Default local test account:

```text
Username: admin
Password: admin123
```

Change this password from the **Change Password** page before actual use.

### LIS Data Upload

The upload module accepts `.xls`, `.xlsx`, and `.csv` exports. During import, the system:

- Detects SF1/LIS columns for LRN, name, sex, and remarks
- Validates 12-digit LRNs
- Stores records by school year and grade level
- Detects duplicate year/grade records
- Detects transfer-related remarks such as `T/I`, `Pending TI`, and `T/O`
- Logs student name, gender, status, and remarks changes

### Student Records

The student module provides:

- Search and filtering
- Student list by LRN, name, grade, school year, and status
- Individual student history pages
- Manual editing of record status and remarks
- Change logs for imported and manually edited values

Manual edits are traceable. The system logs old value, new value, school year, grade level, timestamp, and the admin user who made the change.

### Cohort Tracking

The cohort tracking module follows students across Grade 7 to Grade 10 using LRN. It identifies:

- Straight-path students
- Completed students
- Repeated students
- Transfer-ins
- Transfer-outs
- Missing or incomplete progression
- Dropped or at-risk learners

### Reports

The reports module includes:

- Computed progression rates
- Retention and promotion calculator
- CSR leaving-point breakdown
- At-risk students section
- Excel export
- Print-friendly report page for browser printing or saving as PDF

## Computed Indicators

The system computes:

- **Cohort Survival Rate**: learners reaching Grade 10 compared with the Grade 7 baseline
- **Completion Rate**: learners with Grade 10 records compared with tracked students
- **Retention Rate**: learners retained in the system across records
- **Repetition Rate**: learners appearing in the same grade across different years
- **Promotion Rate**: learners moving from one selected grade/year to the next expected grade/year

The retention and promotion calculator validates that selected year and grade combinations exist before showing results.

## Project Structure

```text
.
+-- app/
|   +-- app.py                  # Main Flask application
|   +-- Dockerfile              # Flask/Gunicorn image
|   +-- requirements.txt        # Python dependencies
|   +-- rsc/                    # Static resources such as school logo
|   +-- templates/              # HTML templates, CSS, and JS assets
+-- db/
|   +-- init.sql                # MySQL schema initialization
+-- web/
|   +-- Dockerfile              # Nginx image
|   +-- default.conf            # Nginx reverse proxy config
+-- docker-compose.yml          # Flask, MySQL, and Nginx services
+-- README.md
```

## Requirements

Install:

- Docker
- Docker Compose

No local Python or MySQL installation is required when running through Docker.

## Getting Started

1. Clone or open the project folder.

```bash
cd my-docker-stack
```

2. Build and start the containers.

```bash
docker compose up -d --build
```

3. Open the app.

```text
http://localhost
```

4. Log in using the default local account.

```text
admin / admin123
```

5. Change the default password from **Account > Change Password**.

## Main Pages

| Page | URL | Purpose |
| --- | --- | --- |
| Login | `/login` | Sign in to the system |
| Dashboard | `/dashboard` | View totals, rates, distribution, and recent changes |
| LIS Upload | `/lis-upload` | Upload LIS/SF1 files and manage imported batches |
| Students | `/students` | Browse, filter, and open student records |
| Student History | `/student/<lrn>` | View progression history and edit status/remarks |
| Cohort Tracking | `/cohort-tracking` | Track a cohort from a selected grade and school year |
| Reports | `/reports` | View computed reports and export files |
| Print Report | `/reports/print` | Print or save report as PDF |
| Change Password | `/change-password` | Update the current admin password |
| Logout | `/logout` | Confirm and end the session |

## Upload Workflow

Recommended import order:

1. Upload Grade 7 records for the starting school year.
2. Upload Grade 8 records for the next school year.
3. Upload Grade 9 records for the next school year.
4. Upload Grade 10 records for the final school year.

Each upload requires:

- School year in `YYYY-YYYY` format
- Grade level from Grade 7 to Grade 10
- LIS/SF1 file

Example:

```text
Grade 7  -> 2022-2023
Grade 8  -> 2023-2024
Grade 9  -> 2024-2025
Grade 10 -> 2025-2026
```

## Data Reset Options

The LIS Upload page includes:

- Delete a selected school year and grade batch
- Delete all records

Use these only when re-importing a corrected dataset or preparing a new test/demo batch.

## Database Tables

The system uses these main tables:

- `students`
- `student_records`
- `student_change_logs`
- `users`

Default Docker database values:

```text
Host: db
Database: mydb
User: root
Password: root
```

For local development outside Docker, update the database connection in `app/app.py`.

## Default Login

The system initializes with a default administrator account:

- **Username:** `admin`
- **Password:** `admin123`

It is strongly recommended to change this password immediately after the first login via the "Change Password" page.

## Useful Docker Commands

Start containers:

```bash
docker compose up -d
```

Rebuild and start:

```bash
docker compose up -d --build
```

View running containers:

```bash
docker compose ps
```

View logs:

```bash
docker compose logs -f
```

Stop containers:

```bash
docker compose down
```

Reset database volume:

```bash
docker compose down -v
docker compose up -d --build
```

## Testing Checklist

Before demo or deployment, verify:

- Login works with the admin account
- Wrong password shows an error
- Logout confirmation appears
- Change password works, then log in again
- LIS upload accepts the client sample files
- Student records appear in `/students`
- Student history shows Grade 7 to Grade 10 movement
- Manual status/remarks edits create change-log entries
- Cohort tracking shows expected completed, transfer, repeated, and incomplete students
- Retention and promotion calculator rejects invalid grade movement
- Reports page shows at-risk students and CSR breakdown
- Excel export downloads successfully
- Print report opens and can be saved as PDF

- The active Flask application is `app/app.py`.
- The main templates are `dashboard.html`, `records_page.html`, `student_history.html`, `reports.html`, `login.html`, `change_password.html`, `logout_confirm.html`, and `print_report.html`.
- Root-level legacy files such as `app.py` or `index.html`, if present, are not used by the Docker setup.
- Bootstrap and Bootstrap Icons are loaded from CDNs, so internet access is needed for those assets unless they are vendored locally.
- The authentication system is suitable for internal use but should be reviewed by a security expert before public deployment.

Deploy this as a **web service**, not a static website.

The app requires:

- Flask/Gunicorn runtime
- MySQL database
- Persistent database storage
- File upload handling
- Server-side sessions

For production deployment, configure a strong secret key:

```text
FLASK_SECRET_KEY=<your-secure-secret>
```

Also change the default admin password immediately after first login.

## Development Notes

- The active Flask app is `app/app.py`.
- The active dashboard template is `app/templates/dashboard.html`.
- Root-level legacy files such as `app.py` and `index.html` are not used by the Dockerized app.
- Bootstrap and Bootstrap Icons are loaded from CDNs.
- Avoid editing LRNs directly. LRN correction should be handled as a separate controlled feature if needed.

## Troubleshooting

If the app cannot connect to MySQL:

```bash
docker compose ps
docker compose logs db
docker compose logs app
```

If tables are missing or schema changes do not appear:

```bash
docker compose down -v
docker compose up -d --build
```

If port `80`, `5000`, or `3306` is already in use, edit the port mappings in `docker-compose.yml`.

If login fails after changing the password, reset the database volume during local testing or update the `users` table manually.

## License

Add a license before publishing this repository publicly.

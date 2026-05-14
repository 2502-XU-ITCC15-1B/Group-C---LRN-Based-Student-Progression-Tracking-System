# Integrated LRN-Based Student Progression Tracking System

A Dockerized Flask web application for importing Learner Information System (LIS) student records, tracking learner progression by LRN, monitoring cohort movement, and exporting progression reports.

The system is designed for school-level student progression monitoring across Grades 7 to 10. It uses Flask for the application layer, MySQL for storage, and Nginx as a reverse proxy.

## Features

- Dashboard summary for total students, transfer-in, transfer-out, repeaters, and progression rates
- LIS/SF1 Excel import for `.xlsx`, `.xls`, and `.csv` files
- LRN-based student records with school year, grade level, gender, status, and remarks
- Automatic detection of transfer-in, pending transfer-in, and transfer-out remarks
- Student search and individual student history pages
- Cohort tracking from a selected starting school year and grade level
- Excel report export for uploaded student progression data
- Batch delete and full reset tools for correcting imported data
- Docker Compose setup with Flask, MySQL, and Nginx

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

## Project Structure

```text
.
+-- app/
|   +-- app.py                  # Main Flask application
|   +-- Dockerfile              # Flask/Gunicorn image
|   +-- requirements.txt        # Python dependencies
|   +-- rsc/                    # Static resources such as logo images
|   +-- templates/              # HTML, CSS, and JavaScript assets
+-- db/
|   +-- init.sql                # MySQL schema initialization
+-- web/
|   +-- Dockerfile              # Nginx image
|   +-- default.conf            # Reverse proxy configuration
+-- docker-compose.yml          # Multi-container setup
+-- README.md
```

## Requirements

Before running the project, install:

- Docker
- Docker Compose

No local Python or MySQL installation is required when using Docker.

## Getting Started

1. Clone the repository:

```bash
git clone <your-repository-url>
cd my-docker-stack
```

2. Build and start the containers:

```bash
docker compose up --build
```

3. Open the application in your browser:

```text
http://localhost
```

The Flask app is also exposed directly at:

```text
http://localhost:5000
```

## Main Pages

| Page | URL | Purpose |
| --- | --- | --- |
| Dashboard | `/dashboard` | View student totals, progression rates, distribution, and recent activity |
| LIS Upload | `/lis-upload` | Upload LIS/SF1 files and manage imported batches |
| Students | `/students` | Browse and filter student records |
| Cohort Tracking | `/cohort-tracking` | Track a learner cohort across school years |
| Reports | `/reports` | View and export progression reports |
| Login Screen | `/login` | Entry screen for the dashboard |

## Uploading LIS Data

Go to `/lis-upload`, then provide:

- School year in `YYYY-YYYY` format, for example `2025-2026`
- Grade level from Grade 7 to Grade 10
- LIS/SF1 file in `.xlsx`, `.xls`, or `.csv` format

The importer looks for these fields:

| Field | Use |
| --- | --- |
| LRN | Primary learner identifier |
| Name | Student master record |
| Sex / Gender | Student profile and reporting |
| Remarks | Transfer and movement status detection |

LRNs are expected to be 12 digits.

## Reports

Reports can be viewed from `/reports`. To export an Excel report, use the report export action from the page or visit:

```text
/reports/export
```

If a school year filter is selected, the exported file is scoped to that year.

## Database

The MySQL database is initialized from `db/init.sql` and creates these tables:

- `students`
- `student_records`
- `student_change_logs`

The default Docker database settings are:

```text
Host: db
Database: mydb
User: root
Password: root
```

For local development outside Docker, update the database connection in `app/app.py`.

## Useful Docker Commands

Start the app:

```bash
docker compose up
```

Start and rebuild images:

```bash
docker compose up --build
```

Run in the background:

```bash
docker compose up -d
```

Stop containers:

```bash
docker compose down
```

Stop containers and remove the database volume:

```bash
docker compose down -v
```

View logs:

```bash
docker compose logs -f
```

## Development Notes

- The active Flask application is `app/app.py`.
- The active dashboard template is `app/templates/index.html`.
- Root-level legacy files such as `app.py` or `index.html`, if present, are not used by the Docker setup.
- Bootstrap and Bootstrap Icons are loaded from CDNs, so internet access is needed for those assets unless they are vendored locally.
- The current login screen is an entry page and should not be treated as production-grade authentication.

## Branching Guide

Recommended branch structure:

- `main`: stable, deployable code
- `staging`: acceptance testing branch
- `develop`: integration branch
- `feature/<feature-name>`: feature branches, for example `feature/login` or `feature/reports`

## Troubleshooting

If the app cannot connect to the database, make sure all containers are running:

```bash
docker compose ps
```

If database tables are missing, recreate the database volume:

```bash
docker compose down -v
docker compose up --build
```

If port `80`, `5000`, or `3306` is already in use, edit the port mappings in `docker-compose.yml`.

## License

Add your preferred license before publishing this repository publicly.

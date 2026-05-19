from .constants import SCHOOL_YEAR_PATTERN


def is_valid_school_year(school_year):
    if not school_year or not SCHOOL_YEAR_PATTERN.match(school_year):
        return False

    start_year, end_year = [int(part) for part in school_year.split("-")]
    return end_year == start_year + 1

CREATE TABLE study (
    study_id TEXT PRIMARY KEY,
    study_name TEXT NOT NULL UNIQUE,
    sponsor TEXT NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('Phase 1', 'Phase 2', 'Phase 3', 'Phase 4')),
    enrollment INTEGER NOT NULL CHECK (enrollment >= 0),
    p_i TEXT NOT NULL
);

CREATE TABLE sites (
    site_id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES study(study_id),
    site_name TEXT NOT NULL UNIQUE,
    site_region VARCHAR(100) NOT NULL,
    site_pi TEXT NOT NULL
);

CREATE TABLE patients(
    patient_id INTEGER PRIMARY KEY NOT NULL,
    site_id TEXT NOT NULL REFERENCES sites(site_id),
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    age INTEGER NOT NULL CHECK (age >= 0),
    sex Char(1) NOT NULL
    current_conditions TEXT,
    meds TEXT,
    allergies TEXT,
    enrollment_status TEXT NOT NULL CHECK (enrollment_status IN ('Enrolled', 'Withdrawn', 'Completed'))
);

CREATE TABLE visits(
    visit_id INTEGER PRIMARY KEY NOT NULL,
    patient_id INTEGER NOT NULL REFERENCES patients(patient_id),
    visit_date DATE NOT NULL,
    visit_type TEXT NOT NULL CHECK (visit_type IN ('Screening', 'Baseline', 'Follow-up', 'Final')),
    visit_notes TEXT
);

CREATE TABLE adverse_events(
    event_id INTEGER PRIMARY KEY NOT NULL,
    patient_id INTEGER NOT NULL REFERENCES patients(patient_id),
    event_date DATE NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('Mild', 'Moderate', 'Severe')),
    event_description TEXT NOT NULL
    ctcae_grade INTEGER NOT NULL CHECK (ctcae_grade BETWEEN 1 AND 5)
);

CREATE TABLE deviations();

CREATE TABLE audit_trail();
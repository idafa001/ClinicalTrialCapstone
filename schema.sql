BEGIN;


CREATE TABLE study (
    study_id VARCHAR(20) PRIMARY KEY,
    study_name TEXT NOT NULL,
    sponsor TEXT NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('Phase 1', 'Phase 2', 'Phase 3', 'Phase 4')),
    enrollment INTEGER NOT NULL,
    p_i TEXT NOT NULL
);

CREATE TABLE sites (
    site_id VARCHAR(20) PRIMARY KEY,
    study_id VARCHAR(20) NOT NULL REFERENCES study(study_id),
    site_name TEXT NOT NULL,
    site_region TEXT NOT NULL,
    site_pi TEXT NOT NULL
);

CREATE TABLE patients (
    subj_id VARCHAR(20) PRIMARY KEY,
    site_id VARCHAR(20) NOT NULL REFERENCES sites(site_id),
    age INTEGER NOT NULL CHECK (screening_age BETWEEN 18 AND 75),
    sex CHAR(1) NOT NULL CHECK (sex IN ('M', 'F', 'U')),
    screening_date DATE NOT NULL,
    ecog_status INTEGER NOT NULL CHECK (ecog_status BETWEEN 0 AND 4),
    enrollment_status TEXT NOT NULL CHECK (
        enrollment_status IN ('SCREENING', 'ENROLLED', 'SCREEN_FAIL', 'WITHDRAWN', 'COMPLETED')
    ),
    enrollment_date DATE
);

-- Protocol schedule: one row per visit type. target_day is an offset from the patient's screening_date (day 0), not a calendar date.
CREATE TABLE visit_schedule (
    visit_type TEXT PRIMARY KEY CHECK (visit_type IN ('Screening', 'Baseline', 'Follow-up', 'Final')),
    target_day INTEGER NOT NULL CHECK (target_day >= 0),
    window_before_days INTEGER NOT NULL CHECK (window_before_days >= 0),
    window_after_days INTEGER NOT NULL CHECK (window_after_days >= 0)
);

INSERT INTO visit_schedule (visit_type, target_day, window_before_days, window_after_days)
VALUES
    ('Screening', 0, 0, 0),
    ('Baseline', 14, 3, 3),
    ('Follow-up', 42, 7, 7),
    ('Final', 84, 7, 7);

-- Planned_visit = patients.screening_date + visit_schedule.target_day, based on visit type
-- actual_visit stays NULL until the visit happens.

CREATE TABLE visits (
    visit_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subj_id VARCHAR(20) NOT NULL REFERENCES patients(subj_id),
    visit_type TEXT NOT NULL REFERENCES visit_schedule(visit_type),
    planned_visit DATE NOT NULL,
    actual_visit DATE,
    visit_notes TEXT,
    UNIQUE (subj_id, visit_type)
);

CREATE TABLE lab_results (
    lab_id  INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    visit_id INTEGER NOT NULL REFERENCES visits(visit_id),
    test_name TEXT NOT NULL,
    result TEXT NOT NULL,
    unit TEXT NOT NULL,
    reference_range TEXT
);

CREATE TABLE adverse_events (
    ae_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subj_id VARCHAR(20) NOT NULL REFERENCES patients(subj_id),
    start_date DATE NOT NULL,
    end_date DATE,
    ctcae_grade INTEGER NOT NULL CHECK (ctcae_grade BETWEEN 1 AND 5),
    action_taken TEXT NOT NULL CHECK (
        action_taken IN ('None', 'Medication', 'Hospitalization', 'Other')
    ),
    outcome TEXT NOT NULL CHECK (
        outcome IN ('Recovered', 'Recovering', 'Not Recovered', 'Fatal', 'Unknown')
    ),
    CHECK (end_date IS NULL OR end_date >= start_date)
);
CREATE TABLE deviations (
    dev_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    visit_id INTEGER NOT NULL REFERENCES visits(visit_id),
    dev_date DATE NOT NULL,
    dev_type TEXT NOT NULL CHECK (dev_type IN ('Protocol', 'Regulatory', 'Other')),
    dev_severity TEXT NOT NULL CHECK (dev_severity IN ('Minor', 'Major', 'Critical')),
    dev_notes TEXT
);

CREATE INDEX sites_study_idx ON sites (study_id);
CREATE INDEX patients_site_idx ON patients (site_id);
CREATE INDEX adverse_events_subj_idx ON adverse_events (subj_id);
CREATE INDEX deviations_visit_idx ON deviations (visit_id);

CREATE TABLE audit_trail (
    audit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subj_id Varchar(20) REFERENCES patients(subj_id) ON DELETE SET NULL,
    target_table TEXT NOT NULL,
    record_id TEXT NOT NULL,
    action_type TEXT NOT NULL CHECK (action_type IN ('INSERT', 'UPDATE', 'DELETE')),
    old_val TEXT,
    new_val TEXT,
    changed_by TEXT NOT NULL DEFAULT CURRENT_USER,
    time_stamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reason_for_change TEXT NOT NULL
);

CREATE FUNCTION capture_audit_change() RETURNS TRIGGER AS $$
DECLARE
    old_row JSONB;
    new_row JSONB;
    current_row JSONB;
    audit_subj_id TEXT;
    record_key TEXT;
    actor TEXT;
    change_reason TEXT;
    changed_column TEXT;
BEGIN
    actor := COALESCE(NULLIF(current_setting('app.changed_by', TRUE), ''), CURRENT_USER);
    change_reason := NULLIF(current_setting('app.change_reason', TRUE), '');

    IF TG_OP = 'INSERT' THEN
        new_row := TO_JSONB(NEW);
    ELSIF TG_OP = 'UPDATE' THEN
        old_row := TO_JSONB(OLD);
        new_row := TO_JSONB(NEW);
    ELSE
        old_row := TO_JSONB(OLD);
    END IF;

    current_row := CASE WHEN TG_OP = 'DELETE' THEN old_row ELSE new_row END;
    -- Use each table's own primary key; a fixed COALESCE order would pick
    -- foreign keys (e.g. study_id on sites, subj_id on visits) instead.
    record_key := COALESCE(
        current_row ->> CASE TG_TABLE_NAME
            WHEN 'study' THEN 'study_id'
            WHEN 'sites' THEN 'site_id'
            WHEN 'patients' THEN 'subj_id'
            WHEN 'visit_schedule' THEN 'visit_type'
            WHEN 'visits' THEN 'visit_id'
            WHEN 'adverse_events' THEN 'ae_id'
            WHEN 'deviations' THEN 'dev_id'
        END,
        'unknown'
    );
    audit_subj_id := COALESCE(new_row ->> 'subj_id', old_row ->> 'subj_id');
    IF TG_TABLE_NAME = 'patients' AND TG_OP = 'DELETE' THEN
        audit_subj_id := NULL;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        FOR changed_column IN SELECT key FROM JSONB_EACH(new_row)
        LOOP
            IF old_row -> changed_column IS DISTINCT FROM new_row -> changed_column THEN
                INSERT INTO audit_trail (
                    subj_id, target_table, record_id, action_type, column_name,
                    old_value, new_value, changed_by, reason_for_change
                ) VALUES (
                    audit_subj_id, TG_TABLE_NAME, record_key, TG_OP, changed_column,
                    old_row ->> changed_column, new_row ->> changed_column, actor, change_reason
                );
            END IF;
        END LOOP;
    ELSE
        INSERT INTO audit_trail (
            subj_id, target_table, record_id, action_type, old_value, new_value,
            changed_by, reason_for_change
        ) VALUES (
            audit_subj_id, TG_TABLE_NAME, record_key, TG_OP, old_row::TEXT, new_row::TEXT,
            actor, change_reason
        );
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION reject_audit_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_trail is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER study_audit AFTER INSERT OR UPDATE OR DELETE ON study
FOR EACH ROW EXECUTE FUNCTION capture_audit_change();
CREATE TRIGGER sites_audit AFTER INSERT OR UPDATE OR DELETE ON sites
FOR EACH ROW EXECUTE FUNCTION capture_audit_change();
CREATE TRIGGER patients_audit AFTER INSERT OR UPDATE OR DELETE ON patients
FOR EACH ROW EXECUTE FUNCTION capture_audit_change();
CREATE TRIGGER visit_schedule_audit AFTER INSERT OR UPDATE OR DELETE ON visit_schedule
FOR EACH ROW EXECUTE FUNCTION capture_audit_change();
CREATE TRIGGER visits_audit AFTER INSERT OR UPDATE OR DELETE ON visits
FOR EACH ROW EXECUTE FUNCTION capture_audit_change();
CREATE TRIGGER adverse_events_audit AFTER INSERT OR UPDATE OR DELETE ON adverse_events
FOR EACH ROW EXECUTE FUNCTION capture_audit_change();
CREATE TRIGGER deviations_audit AFTER INSERT OR UPDATE OR DELETE ON deviations
FOR EACH ROW EXECUTE FUNCTION capture_audit_change();

CREATE TRIGGER audit_trail_append_only BEFORE UPDATE OR DELETE ON audit_trail
FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();

COMMIT;
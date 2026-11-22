"""Generate reproducible synthetic records for Protocol HEM-2026."""

import argparse
import os
import random
from datetime import date, timedelta

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values


SCREEN_FAIL_REASONS = [
	"Eligibility criteria not met",
	"Required baseline assessment unavailable",
	"Participant withdrew before enrollment",
]
AE_TERMS = [
	"Febrile neutropenia",
	"Anemia",
	"Thrombocytopenia",
	"Nausea",
	"Fatigue",
]


def connect_database():
	load_dotenv()
	password = os.getenv("PGPASSWORD")
	if not password:
		raise RuntimeError("Set PGPASSWORD in your local .env file before connecting.")
	return psycopg2.connect(
		host=os.getenv("PGHOST", "localhost"),
		port=os.getenv("PGPORT", "5432"),
		dbname=os.getenv("PGDATABASE", "hematology_trial"),
		user=os.getenv("PGUSER", "trial_admin"),
		password=password,
	)


def make_subjects(rng, seed, count, as_of):
	statuses = ["ENROLLED", "WITHDRAWN", "COMPLETED", "SCREEN_FAIL", "SCREENING"]
	weights = [62, 8, 10, 15, 5]
	subjects = []

	for number in range(1, count + 1):
		subj_id = f"HEM-{seed}-S{number:04d}"
		screening_date = as_of - timedelta(days=rng.randint(120, 360))
		status = rng.choices(statuses, weights=weights, k=1)[0]
		enrollment_date = None
		if status in {"ENROLLED", "WITHDRAWN", "COMPLETED"}:
			enrollment_date = screening_date + timedelta(days=rng.randint(1, 14))

		anc = None
		if enrollment_date and rng.random() >= 0.08:
			anc = round(rng.triangular(0.0, 8.0, 1.2), 2)

		subjects.append(
			{
				"subj_id": subj_id,
				"site_id": f"HEM-{seed}-SITE{((number - 1) % 3) + 1:02d}",
				"screening_age": rng.randint(18, 75),
				"sex": rng.choice(["M", "F", "U"]),
				"baseline_anc": anc,
				"screening_date": screening_date,
				"enrollment_date": enrollment_date,
				"screen_fail_reason": (
					rng.choice(SCREEN_FAIL_REASONS) if status == "SCREEN_FAIL" else None
				),
				"enrollment_status": status,
			}
		)
	return subjects


def main():
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--seed", type=int, default=2026)
	parser.add_argument("--subjects", type=int, default=120)
	parser.add_argument("--as-of", default=date.today().isoformat())
	args = parser.parse_args()
	if args.subjects < 1:
		parser.error("--subjects must be at least 1")

	rng = random.Random(args.seed)
	as_of = date.fromisoformat(args.as_of)
	study_id = f"HEM-2026-{args.seed}"
	site_rows = [
		(f"HEM-{args.seed}-SITE01", study_id, "North Research Center", "Northeast", "Dr. A. Rivera"),
		(f"HEM-{args.seed}-SITE02", study_id, "Central Hematology Clinic", "Central", "Dr. M. Chen"),
		(f"HEM-{args.seed}-SITE03", study_id, "Lakeside Cancer Institute", "Midwest", "Dr. S. Patel"),
	]
	subjects = make_subjects(rng, args.seed, args.subjects, as_of)
	enrolled_count = sum(
		subject["enrollment_status"] in {"ENROLLED", "WITHDRAWN", "COMPLETED"}
		for subject in subjects
	)

	connection = connect_database()
	try:
		with connection:
			with connection.cursor() as cursor:
				cursor.execute("SELECT 1 FROM study WHERE study_id = %s", (study_id,))
				if cursor.fetchone():
					raise RuntimeError(
						f"Study {study_id} already exists. Use a new --seed or reset your local demo database."
					)

				cursor.execute(
					"SELECT set_config('app.changed_by', %s, TRUE), "
					"set_config('app.change_reason', %s, TRUE)",
					("synthetic_generator", "Generated synthetic capstone records"),
				)
				cursor.execute(
					"INSERT INTO study (study_id, study_name, sponsor, phase, enrollment, p_i) "
					"VALUES (%s, %s, %s, %s, %s, %s)",
					(
						study_id,
						f"Protocol HEM-2026 Synthetic Trial {args.seed}",
						"Synthetic Sponsor",
						"Phase 2",
						enrolled_count,
						"Dr. Jordan Lee",
					),
				)
				execute_values(
					cursor,
					"INSERT INTO sites (site_id, study_id, site_name, site_region, site_pi) VALUES %s",
					site_rows,
				)
				subject_rows = [
					(
						item["subj_id"],
						item["site_id"],
						item["screening_age"],
						item["sex"],
						item["baseline_anc"],
						item["screening_date"],
						item["enrollment_date"],
						item["screen_fail_reason"],
						item["enrollment_status"],
					)
					for item in subjects
				]
				execute_values(
					cursor,
					"INSERT INTO patients (subj_id, site_id, screening_age, sex, baseline_anc, "
					"screening_date, enrollment_date, screen_fail_reason, enrollment_status) VALUES %s",
					subject_rows,
				)

				cursor.execute(
					"SELECT visit_type, target_day, window_before_days, window_after_days "
					"FROM visit_schedule"
				)
				schedule = cursor.fetchall()
				visit_rows = []
				for subject in subjects:
					if subject["enrollment_status"] == "SCREEN_FAIL":
						allowed_visits = {"Screening"}
					elif subject["enrollment_status"] == "SCREENING":
						allowed_visits = {"Screening"}
					else:
						allowed_visits = {row[0] for row in schedule}

					for visit_type, target_day, before_days, after_days in schedule:
						if visit_type not in allowed_visits:
							continue
						planned = subject["screening_date"] + timedelta(days=target_day)
						if visit_type == "Screening" or rng.random() >= 0.05:
							if visit_type == "Screening":
								offset = 0
							elif rng.random() < 0.12:
								offset = after_days + rng.randint(1, 5)
							else:
								offset = rng.randint(-before_days, after_days)
							actual = planned + timedelta(days=offset)
						else:
							actual = None
						visit_rows.append(
							(subject["subj_id"], visit_type, planned, actual, None)
						)

				execute_values(
					cursor,
					"INSERT INTO visits (subj_id, visit_type, planned_visit, actual_visit, visit_notes) VALUES %s",
					visit_rows,
				)

				cursor.execute(
					"SELECT visit_id, subj_id, planned_visit, actual_visit "
					"FROM visits WHERE subj_id = ANY(%s)",
					([subject["subj_id"] for subject in subjects],),
				)
				visits = cursor.fetchall()

				ae_rows = []
				enrolled_subjects = [item for item in subjects if item["enrollment_date"]]
				for subject in enrolled_subjects:
					if rng.random() >= 0.35:
						continue
					start = subject["enrollment_date"] + timedelta(
						days=rng.randint(0, max(0, (as_of - subject["enrollment_date"]).days))
					)
					end = None if rng.random() < 0.25 else start + timedelta(days=rng.randint(1, 21))
					ae_rows.append(
						(
							subject["subj_id"],
							start,
							end,
							rng.choice(AE_TERMS),
							rng.randint(1, 5),
							rng.choice(["None", "Medication", "Hospitalization", "Other"]),
							rng.choice(["Recovered", "Recovering", "Not Recovered", "Unknown"]),
						)
					)
				if ae_rows:
					execute_values(
						cursor,
						"INSERT INTO adverse_events (subj_id, ae_start_date, ae_end_date, ae_description, "
						"ctcae_grade, ae_action_taken, ae_outcome) VALUES %s",
						ae_rows,
					)

				deviation_rows = []
				for visit_id, _subj_id, planned, actual in visits:
					if rng.random() < 0.08:
						deviation_rows.append(
							(
								visit_id,
								actual or planned,
								rng.choice(["Protocol", "Regulatory", "Other"]),
								rng.choice(
									[
										"Visit completed outside the preferred window",
										"Required source document was incomplete",
										"Scheduled assessment was omitted",
									]
								),
								rng.choice(["Minor", "Major", "Critical"]),
							)
						)
				if deviation_rows:
					execute_values(
						cursor,
						"INSERT INTO deviations (visit_id, dev_date, dev_type, dev_description, dev_severity) VALUES %s",
						deviation_rows,
					)

		print(f"Generated study {study_id}: {len(subjects)} synthetic subjects across 3 sites.")
		print(f"Created {len(visit_rows)} visits, {len(ae_rows)} adverse events, and {len(deviation_rows)} deviations.")
		print("All records are synthetic. The generator uses the supplied seed for repeatable values.")
	finally:
		connection.close()


if __name__ == "__main__":
	main()

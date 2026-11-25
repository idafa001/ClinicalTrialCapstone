"""Run data-quality checks and generate simple capstone report outputs."""

import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import psycopg2
from dotenv import load_dotenv


REPORT_DIR = Path(__file__).resolve().parent / "reports"


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


def read_frame(connection, query):
	return pd.read_sql_query(query, connection)


def collect_findings(connection):
	checks = [
		(
			"MISSING_BASELINE_ANC",
			"WARNING",
			"patients",
			"""
			SELECT subj_id AS record_id,
				   'Enrolled subject has no baseline ANC result.' AS finding_detail
			FROM patients
			WHERE enrollment_status IN ('ENROLLED', 'WITHDRAWN', 'COMPLETED')
			  AND baseline_anc IS NULL
			""",
		),
		(
			"VISIT_OUT_OF_WINDOW",
			"ERROR",
			"visits",
			"""
			SELECT v.visit_id::TEXT AS record_id,
				   format('%s visit was on %s; allowed window is %s through %s.',
						  v.visit_type, v.actual_visit,
						  v.planned_visit - s.window_before_days,
						  v.planned_visit + s.window_after_days) AS finding_detail
			FROM visits AS v
			JOIN visit_schedule AS s USING (visit_type)
			WHERE v.actual_visit IS NOT NULL
			  AND (v.actual_visit < v.planned_visit - s.window_before_days
				   OR v.actual_visit > v.planned_visit + s.window_after_days)
			""",
		),
		(
			"PAST_VISIT_MISSING",
			"WARNING",
			"visits",
			"""
			SELECT v.visit_id::TEXT AS record_id,
				   format('%s visit passed its allowed window but has no actual date.', v.visit_type)
				   AS finding_detail
			FROM visits AS v
			JOIN visit_schedule AS s USING (visit_type)
			WHERE v.actual_visit IS NULL
			  AND v.planned_visit + s.window_after_days < CURRENT_DATE
			""",
		),
		(
			"VISIT_BEFORE_SCREENING",
			"ERROR",
			"visits",
			"""
			SELECT v.visit_id::TEXT AS record_id,
				   'Actual visit date is earlier than the subject screening date.' AS finding_detail
			FROM visits AS v
			JOIN patients AS p USING (subj_id)
			WHERE v.actual_visit < p.screening_date
			""",
		),
	]

	findings = []
	for rule_code, severity, target_table, query in checks:
		frame = read_frame(connection, query)
		for record in frame.to_dict(orient="records"):
			findings.append(
				{
					"rule_code": rule_code,
					"target_table": target_table,
					"record_id": str(record["record_id"]),
					"severity": severity,
					"finding_detail": record["finding_detail"],
				}
			)
	return pd.DataFrame(
		findings,
		columns=["rule_code", "target_table", "record_id", "severity", "finding_detail"],
	)


def save_plot(frame, title, filename, kind="bar"):
	figure, axis = plt.subplots(figsize=(9, 5))
	if frame.empty:
		axis.text(0.5, 0.5, "No records to display", ha="center", va="center")
		axis.set_axis_off()
	elif kind == "line":
		frame.plot(ax=axis, marker="o")
		axis.set_ylabel("Subjects enrolled")
	elif kind == "barh":
		frame.plot(kind="barh", ax=axis, legend=False, color="#287271")
		axis.set_xlabel("Subjects")
	else:
		frame.plot(kind="bar", ax=axis)
		axis.set_ylabel("Deviation count")
	axis.set_title(title)
	figure.tight_layout()
	figure.savefig(REPORT_DIR / filename, dpi=150)
	plt.close(figure)


def make_reports(connection):
	REPORT_DIR.mkdir(exist_ok=True)
	enrollment = read_frame(
		connection,
		"""
		SELECT date_trunc('month', p.enrollment_date)::DATE AS enrollment_month,
			   s.site_name,
			   COUNT(*) AS subject_count
		FROM patients AS p
		JOIN sites AS s USING (site_id)
		WHERE p.enrollment_date IS NOT NULL
		GROUP BY 1, 2
		ORDER BY 1, 2
		""",
	)
	enrollment.to_csv(REPORT_DIR / "enrollment_over_time.csv", index=False)
	if enrollment.empty:
		enrollment_plot = pd.DataFrame()
	else:
		enrollment_plot = enrollment.pivot(
			index="enrollment_month", columns="site_name", values="subject_count"
		).fillna(0)
	save_plot(enrollment_plot, "Enrollment over time by site", "enrollment_over_time.png", "line")

	deviations = read_frame(
		connection,
		"""
		SELECT s.site_name, d.dev_type, COUNT(*) AS deviation_count
		FROM deviations AS d
		JOIN visits AS v USING (visit_id)
		JOIN patients AS p USING (subj_id)
		JOIN sites AS s USING (site_id)
		GROUP BY 1, 2
		ORDER BY 1, 2
		""",
	)
	deviations.to_csv(REPORT_DIR / "deviations_by_type_site.csv", index=False)
	if deviations.empty:
		deviation_plot = pd.DataFrame()
	else:
		deviation_plot = deviations.pivot(
			index="site_name", columns="dev_type", values="deviation_count"
		).fillna(0)
	save_plot(deviation_plot, "Deviations by type and site", "deviations_by_type_site.png")

	screen_fails = read_frame(
		connection,
		"""
		SELECT screen_fail_reason, COUNT(*) AS subject_count
		FROM patients
		WHERE enrollment_status = 'SCREEN_FAIL'
		GROUP BY screen_fail_reason
		ORDER BY subject_count DESC, screen_fail_reason
		""",
	)
	screen_fails.to_csv(REPORT_DIR / "screen_fail_reasons.csv", index=False)
	screen_fail_plot = (
		screen_fails.set_index("screen_fail_reason")["subject_count"]
		if not screen_fails.empty
		else pd.DataFrame()
	)
	save_plot(screen_fail_plot, "Screen-fail reasons", "screen_fail_reasons.png", "barh")


def main():
	connection = connect_database()
	try:
		findings = collect_findings(connection)
		REPORT_DIR.mkdir(exist_ok=True)
		findings.to_csv(REPORT_DIR / "dq_findings.csv", index=False)
		make_reports(connection)
		with connection.cursor() as cursor:
			cursor.execute("SELECT COUNT(*) FROM audit_trail")
			audit_count = cursor.fetchone()[0]
		print(f"Data-quality check: {len(findings)} finding(s).")
		print(f"Append-only audit rows recorded: {audit_count}.")
		print(f"CSV summaries and PNG charts saved in: {REPORT_DIR}")
	finally:
		connection.close()


if __name__ == "__main__":
	main()

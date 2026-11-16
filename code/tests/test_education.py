import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import main
from backend.education.facts import build_education_facts
from backend.education.job_requirements import extract_job_requirements
from backend.education.models import AdditionalEducation
from backend.education.ranking import normalize_rank
from backend.education.scoring import EducationScorer, ranking_period_label
from backend.education.university import resolve_university
from backend.schema import Profile


def profile(education, awards=()):
    return Profile.model_validate(
        {
            "bio": {"name": "Alex Example"},
            "education": education,
            "work_experience": [],
            "achievements_awards": list(awards),
            "publications": [],
        }
    )


def ai_job():
    return extract_job_requirements(
        "Assistant Professor in Artificial Intelligence. Applicants must hold a PhD "
        "in Computer Science or a closely related field. Research interests include "
        "LLMs, machine learning, and AI agents."
    )


class JobRequirementsTests(unittest.TestCase):
    def test_extracts_degree_field_and_research_areas(self):
        job = ai_job()
        self.assertEqual(job.minimum_degree, "PhD")
        self.assertIn("Computer Science", job.required_fields)
        self.assertIn("Artificial Intelligence", job.required_fields)
        self.assertIn("Machine Learning", job.research_areas)
        self.assertIn("AI Agents", job.research_areas)

    def test_required_vs_preferred_degree_is_distinguished(self):
        job = extract_job_requirements("本科必需，博士优先。研究领域为人工智能。")
        self.assertEqual(job.minimum_degree, "Bachelor's")
        self.assertEqual(job.preferred_degree, "PhD")
        self.assertEqual(job.degree_requirement_kind, "required_and_preferred")

    def test_degree_name_field_is_extracted_without_department(self):
        facts = build_education_facts(
            profile(
                [
                    {
                        "degree": "PhD in Computer Science",
                        "institution": "Stanford University",
                        "period": "2018-2022",
                    }
                ]
            ),
            "cand-field",
        )
        self.assertEqual(facts.degrees[0].field, "Computer Science")


class EducationScoringTests(unittest.TestCase):
    def setUp(self):
        self.scorer = EducationScorer()

    def test_phd_directly_relevant_scores_ten(self):
        facts = build_education_facts(
            profile(
                [
                    {
                        "degree": "PhD",
                        "institution": "Stanford University",
                        "department": "Computer Science",
                        "period": "2018-2022",
                        "thesis": "Retrieval-Augmented Generation for Large Language Models",
                    }
                ]
            ),
            "cand-001",
        )
        assessment = self.scorer.assess(facts, ai_job(), "job-001")
        self.assertEqual(assessment.education_score.components["degree_level"].score, 10)
        self.assertEqual(assessment.education_score.components["field_relevance"].score, 10)
        self.assertEqual(assessment.qualification_check.status, "MET")

    def test_masters_when_phd_required_is_not_met_and_scores_seven(self):
        facts = build_education_facts(
            profile(
                [
                    {
                        "degree": "Master's",
                        "institution": "Massachusetts Institute of Technology",
                        "department": "Computer Science",
                        "period": "2016-2018",
                        "thesis": None,
                    }
                ]
            ),
            "cand-002",
        )
        job = extract_job_requirements("Applicants must hold a PhD in Computer Science. Research: AI.")
        assessment = self.scorer.assess(facts, job, "job-002")
        self.assertEqual(assessment.education_score.components["degree_level"].score, 7)
        self.assertEqual(assessment.qualification_check.status, "NOT MET")
        self.assertIs(assessment.qualification_check.minimum_degree_met, False)

    def test_in_progress_doctorate_is_not_counted_as_awarded(self):
        facts = build_education_facts(
            profile(
                [
                    {
                        "degree": "PhD",
                        "institution": "Stanford University",
                        "department": "Computer Science",
                        "period": "2024-Present",
                        "thesis": None,
                    }
                ]
            ),
            "cand-in-progress",
        )
        self.assertEqual(facts.degrees[0].status, "in_progress")
        assessment = self.scorer.assess(facts, ai_job(), "job-in-progress")
        self.assertEqual(assessment.qualification_check.minimum_degree_met, False)
        self.assertEqual(assessment.qualification_check.in_progress_degree, "PhD")
        self.assertNotEqual(assessment.qualification_check.status, "MET")
        self.assertNotIn("Highest completed degree: PhD", assessment.qualification_check.explanation)

    def test_missing_dissertation_is_not_found_and_not_zero(self):
        facts = build_education_facts(
            profile(
                [
                    {
                        "degree": "PhD",
                        "institution": "University of Nowhere",
                        "department": "Computer Science",
                        "period": "2015-2021",
                        "thesis": None,
                    }
                ]
            ),
            "cand-003",
        )
        self.assertEqual(facts.dissertation_status, "not_found")
        assessment = self.scorer.assess(facts, ai_job(), "job-003")
        component = assessment.education_score.components["dissertation_relevance"]
        self.assertEqual(component.status, "not_found")
        self.assertIsNone(component.score)

    def test_university_absent_from_ranking_is_null_not_zero(self):
        facts = build_education_facts(
            profile(
                [
                    {
                        "degree": "PhD",
                        "institution": "Unranked University",
                        "department": "Computer Science",
                        "period": "2015-2021",
                        "thesis": "Some topic",
                    }
                ]
            ),
            "cand-004",
        )
        assessment = self.scorer.assess(facts, ai_job(), "job-004")
        overall = assessment.education_score.components["overall_university_strength"]
        self.assertEqual(overall.status, "not_found")
        self.assertIsNone(overall.score)

    def test_unrelated_additional_education_gets_no_positive_contribution(self):
        facts = build_education_facts(
            profile(
                [
                    {
                        "degree": "PhD",
                        "institution": "Stanford University",
                        "department": "Computer Science",
                        "period": "2018-2022",
                        "thesis": "Retrieval-Augmented Generation",
                    }
                ]
            ),
            "cand-005",
        )
        facts.additional_education.append(
            AdditionalEducation(
                kind="Certificate",
                field="Culinary Arts",
                institution="Cooking School",
                year=2015,
            )
        )
        component = self.scorer._additional_education(facts, ai_job())
        self.assertLessEqual(component.score, 2)

    def test_ranking_period_labels_current_vs_historical(self):
        self.assertIn("Current", ranking_period_label(2026, 2017))
        self.assertIn("Historical", ranking_period_label(2017, 2017))


class UniversityAndRankingTests(unittest.TestCase):
    def test_mit_alias_resolves_to_canonical(self):
        self.assertEqual(resolve_university("MIT").canonical, "Massachusetts Institute of Technology")

    def test_ambiguous_university_is_flagged(self):
        resolution = resolve_university("University of Washington")
        self.assertTrue(resolution.ambiguous)
        self.assertEqual(resolution.status, "ambiguous")

    def test_normalize_rank_uses_configured_percentiles(self):
        self.assertEqual(normalize_rank("QS", 3, 1500), 10.0)
        self.assertEqual(normalize_rank("QS", 360, 1500), 7.0)

    def test_education_gap_is_flagged_without_inferring_cause(self):
        facts = build_education_facts(
            profile(
                [
                    {"degree": "Bachelor's", "institution": "University A", "period": "2015"},
                    {"degree": "Master's", "institution": "University B", "period": "2020"},
                ]
            ),
            "cand-006",
        )
        self.assertEqual(len(facts.education_gaps), 1)
        gap = facts.education_gaps[0]
        self.assertEqual(gap.duration_years, 5)
        self.assertIn("Reason not stated", gap.note)
        self.assertNotIn("poor", gap.note.lower())


class EducationAPITests(unittest.TestCase):
    def setUp(self):
        workspace = Path(__file__).resolve().parents[2]
        self.storage = workspace / "tmp_education_api_tests"
        shutil.rmtree(self.storage, ignore_errors=True)
        self.storage.mkdir(parents=True, exist_ok=True)
        self.patch_storage = patch.object(main, "STORAGE", self.storage)
        self.patch_jobs = patch.object(main, "JOBS_DIR", self.storage / "jobs")
        self.patch_storage.start()
        self.patch_jobs.start()
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.patch_jobs.stop()
        self.patch_storage.stop()
        shutil.rmtree(self.storage, ignore_errors=True)

    def make_candidate(self):
        candidate_id = "a" * 32
        folder = self.storage / candidate_id
        folder.mkdir(parents=True, exist_ok=True)
        main.write_json(
            folder / "job.json",
            {
                "id": candidate_id,
                "filename": "CV.pdf",
                "extension": ".pdf",
                "status": "completed",
                "name": "Alex Example",
                "institution": "Stanford University",
                "error": None,
                "created_at": main.now(),
                "updated_at": main.now(),
            },
        )
        main.write_json(
            folder / "profile.json",
            {
                "bio": {"name": "Alex Example"},
                "education": [
                    {
                        "degree": "PhD",
                        "institution": "Stanford University",
                        "department": "Computer Science",
                        "period": "2018-2022",
                        "thesis": "Retrieval-Augmented Generation for Large Language Models",
                        "source_pages": [1],
                    }
                ],
                "work_experience": [],
                "achievements_awards": [],
                "publications": [],
            },
        )
        return candidate_id

    def test_job_analyze_assessment_evidence_and_review(self):
        candidate_id = self.make_candidate()
        job_response = self.client.post(
            "/api/jobs",
            json={
                "text": "Assistant Professor in Artificial Intelligence. "
                "Applicants must hold a PhD in Computer Science or a closely related field. "
                "Research interests include LLMs, machine learning, and AI agents."
            },
        )
        self.assertEqual(job_response.status_code, 201)
        job_id = job_response.json()["id"]

        analyze = self.client.post(f"/api/education/analyze/{candidate_id}", json={"job_id": job_id})
        self.assertEqual(analyze.status_code, 200)
        body = analyze.json()
        self.assertEqual(body["qualification_check"]["status"], "MET")
        self.assertIsNotNone(body["education_score"]["final_score"])

        assessment = self.client.get(f"/api/education/assessment/{candidate_id}")
        self.assertEqual(assessment.status_code, 200)
        self.assertEqual(assessment.json()["job_id"], job_id)

        evidence = self.client.get(f"/api/education/evidence/{candidate_id}")
        self.assertEqual(evidence.status_code, 200)
        self.assertGreater(len(evidence.json()["evidence"]), 0)

        review = self.client.post(
            f"/api/education/review/{candidate_id}",
            json={
                "component": "degree_level",
                "reviewer_score": 9,
                "reviewer_id": "reviewer-1",
                "reason": "Adjusted for policy.",
            },
        )
        self.assertEqual(review.status_code, 200)
        reviewed = review.json()
        self.assertEqual(reviewed["review_status"], "REVIEWED")
        self.assertEqual(reviewed["education_score"]["components"]["degree_level"]["score"], 9)
        self.assertEqual(len(reviewed["audit_trail"]), 2)
        self.assertEqual(reviewed["audit_trail"][0]["original_score"], 10)


    def test_job_list_returns_created_jobs(self):
        created = self.client.post(
            "/api/jobs",
            json={"text": "Assistant Professor in Artificial Intelligence. Applicants must hold a PhD.", "title": "Assistant Professor"},
        )
        self.assertEqual(created.status_code, 201)
        job_id = created.json()["id"]

        listed = self.client.get("/api/jobs")
        self.assertEqual(listed.status_code, 200)
        ids = [j["id"] for j in listed.json()]
        self.assertIn(job_id, ids)
        self.assertTrue(all(j["id"] for j in listed.json()))

    def test_reanalyze_creates_new_version_instead_of_overwriting(self):
        candidate_id = self.make_candidate()
        first = self.client.post(
            "/api/jobs",
            json={"text": "Applicants must hold a PhD in Computer Science. Research: AI."},
        ).json()
        second = self.client.post(
            "/api/jobs",
            json={"text": "Applicants must hold a Master's degree in Computer Science."},
        ).json()

        run1 = self.client.post(
            f"/api/education/analyze/{candidate_id}", json={"job_id": first["id"]}
        )
        self.assertEqual(run1.status_code, 200)
        self.assertEqual(run1.json()["version"], 1)

        run2 = self.client.post(
            f"/api/education/analyze/{candidate_id}", json={"job_id": first["id"]}
        )
        self.assertEqual(run2.status_code, 200)
        self.assertEqual(run2.json()["version"], 2)

        latest = self.client.get(f"/api/education/assessment/{candidate_id}")
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(latest.json()["version"], 2)
        self.assertEqual(latest.json()["job_id"], first["id"])

        # A different job starts its own version numbering and becomes current.
        run3 = self.client.post(
            f"/api/education/analyze/{candidate_id}", json={"job_id": second["id"]}
        )
        self.assertEqual(run3.json()["version"], 1)
        self.assertEqual(run3.json()["job_id"], second["id"])


if __name__ == "__main__":
    unittest.main()

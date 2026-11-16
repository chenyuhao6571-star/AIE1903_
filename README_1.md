# AIE-1903 · Candidate Dossier

Python + React demo for reviewing academic candidate dossiers. Collect PDF/DOCX documents, analyze the complete dossier in one explicit DeepSeek request, and assess documented qualifications in a separate request with an editable rubric. Both actions show an estimated cost before running.

## Features

- **Candidate profiles:** education, experience, publications, awards, teaching, research, motivation and links, with references to source documents.
- **Tag search:** filter candidates by research interests, institutions, degrees, roles and publication venues, matching all or any selected tags.
- **Standardized tags:** `code/tags.json` supplies degree levels/statuses and known aliases to the existing analysis request. The server validates allowed values; older profiles get conservative local tags without another call. Degree status remains attached to its qualification, and source references are retained. Restart the server after editing the vocabulary.
- **Manual corrections:** edit profile sections and source references; corrections update tags and exports. Original model output and a before/after audit remain stored separately. Scores continue to use original source documents.
- **Notes:** maintain private review notes on each candidate, including before analysis. Notes are not sent to the model or used for scoring.
- **Delete candidate:** permanently remove a dossier and its documents, notes, assessments and request history after confirmation.
- **GitHub import:** automatically detect profile links in CV text and PDF/DOCX hyperlinks; import public repository metadata and README excerpts for the next dossier analysis.
- **Criteria:** create, edit, import and export JSON templates. Four example templates total 100 points each, including 5 points for GitHub / Open Source.
- **Score:** select a template and review section scores, explanations, evidence and missing information.
- **Score history:** open earlier assessments with their original criteria, date and dossier version; export saved results without another model call.
- **Comparison:** compare current assessments under a shared template.
- **Request history:** inspect model input/output, status, token usage and estimated charges, including failed requests.
- **Interview questions:** generate source-backed questions with DeepSeek from gaps in the dossier; edit, approve and persist questions and answer criteria.
- **Recorded interviews:** save a video, transcribe its audio with Qwen `qwen3.8-omni-flash`, then group actual questions and answers with DeepSeek. Seek by timestamp, filter matches to saved questions, and download the transcript/audio. Completed stages survive page refresh and server restart.
- **Approved question analysis:** search the complete saved transcript for the candidate's answers to current approved questions with DeepSeek. Review verbatim evidence, timestamps and coverage of each criterion. Changed approvals or source text require an explicit new analysis; unchanged results are cached without another audio request.

## Run locally

Requires Python 3.11+ and Node.js/npm. Configure `DEEPSEEK_API_KEY` or `DEEPSEEK_KEY_FILE` before analyzing documents. Interview transcription also needs FFmpeg/ffprobe on PATH and `QWEN_API_KEY` or `QWEN_KEY_FILE`.

```powershell
cd code
.\setup.ps1
.\start.ps1
```

Open [127.0.0.1:8000](http://127.0.0.1:8000). See [setup, workflow, configuration and tests](code/README.md).

For authenticated GitHub requests, set `GITHUB_TOKEN` or place only the token in `github.txt` at the repository root. Use `GITHUB_TOKEN_FILE` for another file location. The environment token takes priority; token files are read on each import.

## Data and behavior

Uploading documents and importing GitHub data do not invoke the LLM. **Analyze dossier** sends the combined evidence once; **Score candidate** makes one separate request. No automatic paid retries occur. Changing documents, GitHub snapshots or scoring rules requires an explicit new analysis or score as applicable.

API keys, `github.txt`, candidate documents, generated profiles, request logs and local template overrides are excluded from Git. Results stay in `code/storage/` by default. The optional sample button uses `data/simple_dataset/CV.pdf` only when that local file exists.

Shipped templates are demo examples, not official institutional criteria. Assessments support human review and do not make hiring decisions. GitHub summaries cover metadata and README excerpts, not a source-code audit. This local demo has no authentication and binds to `127.0.0.1`.

## Tests

From `code/`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
cd frontend
npm.cmd run build
```

Tests use synthetic documents and mocked API responses without spending API balance.

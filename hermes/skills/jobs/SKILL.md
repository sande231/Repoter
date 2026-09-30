---
name: jobs
description: Sandeep's AI-Job-Agent on this machine - find internships, see top job matches, track applications and statuses, upcoming interviews, and daily job reports.
version: 1.0.0
platforms: [linux]
metadata:
  hermes:
    tags: [jobs, internships, applications, personal]
    category: personal
    requires_toolsets: [terminal]
---

# Job Agent

Sandeep's AI-Job-Agent backend runs on this machine at http://127.0.0.1:8000
(code in ~/job-agent). Call it with curl and summarize the JSON for the user.

## When to Use
- The user asks for new jobs or internships, job matches, or job search
- The user asks about their applications, statuses, interviews, or job report
- The user says they applied somewhere, got an interview, an offer, or a rejection

## Read-only calls (safe, run freely)
- Application summary:   curl -s http://127.0.0.1:8000/applications/summary
- All applications:      curl -s http://127.0.0.1:8000/applications
- Saved/discovered jobs: curl -s http://127.0.0.1:8000/discovered-jobs
- Live job preview (free, no AI scoring): curl -s http://127.0.0.1:8000/jobs/live-preview
- Upcoming deadlines:    curl -s http://127.0.0.1:8000/applications/upcoming
- Upcoming interviews:   curl -s http://127.0.0.1:8000/interviews/upcoming
- Daily report:          curl -s http://127.0.0.1:8000/reports/daily
- Preview an auto-filled application (does NOT submit):
  curl -s -X POST http://127.0.0.1:8000/discovered-jobs/<job_id>/prepare-application

## Calls that change data (ask first)
- Discover and AI-score new jobs (uses OpenAI credits):
  curl -s -X POST http://127.0.0.1:8000/jobs/live-discover-and-save
- Move a discovered job into the application tracker:
  curl -s -X POST http://127.0.0.1:8000/discovered-jobs/<job_id>/apply
- Change an application's status (Saved, Applied, Assessment, Interview, Offer, Rejected):
  curl -s -X PUT http://127.0.0.1:8000/applications/<id>/status -H "Content-Type: application/json" -d '{"status":"Interview"}'

Before any of these, tell the user exactly what will happen and wait for a clear yes.

## Procedure
1. For "find jobs": show /discovered-jobs first. If the user wants fresh ones, offer live-discover-and-save and mention it uses OpenAI credits.
2. When listing jobs, show at most 5: title, company, match score, and job id. Highest match score first.
3. When the user says they applied or got news about a job, find the application id with /applications, confirm, then update the status.
4. After a status change, confirm it with /applications/summary.

## Pitfalls
- "Connection refused" means the backend is not running. Offer to start it: ~/synapse/hermes/start_job_agent.sh
- "Please upload your resume first" means no resume is saved yet. Tell the user; do not guess resume details.
- Nothing here submits a real application to an employer. Never claim an application was submitted.
- Never print or reveal the contents of ~/job-agent/backend/.env.

/**
 * Fictional sample data for the "Try sample profile" option.
 *
 * Everything in this file is invented. The candidate, the employers, the
 * university and the job posting do not exist, and no real person's data is
 * included.
 *
 * Generated from backend/fixtures by backend/fixtures/build_frontend_sample.py.
 * Do not edit by hand: change the fixture files and run that script again.
 */

export const SAMPLE_SOURCES: { label: string; source_type: "resume" | "linkedin" | "notes"; text: string }[] = [
  {
    label: "Resume",
    source_type: "resume",
    text: `Jordan Rivera
Columbus, OH | jordan.rivera@example.com | (614) 555-0142 | https://example.com/in/jordan-rivera

Summary
Machine learning engineer with four years of software experience building Python services, retrieval features, and model evaluation tooling. Comfortable across the stack, from PyTorch model code to FastAPI backends and React front ends.

Experience
Machine Learning Engineer - Brightloom Labs (Aug 2024 - Present)
- Built a retrieval-augmented generation (RAG) service in Python and FastAPI that answers support questions over 40,000 help-center articles
- Fine-tuned a PyTorch text classifier on 8,500 labelled support tickets, raising macro F1 from 0.81 to 0.88
- Created an offline evaluation harness with 300 graded question-answer pairs to compare LLM prompt and retrieval changes before release
- Packaged model services as Docker images and cut image size from 1.4 GB to 380 MB with multi-stage builds

Software Engineer - Quillfeather Software (Jul 2022 - Jul 2024)
- Reduced median API response time from 420 ms to 290 ms by adding Redis caching and PostgreSQL indexes
- Improved unit test coverage by 20% by adding pytest suites for the billing and export modules
- Built React and TypeScript dashboards used by 35 internal support agents
- Cut a nightly reporting job from 3 hours to 45 minutes by batching SQL queries
- Set up GitHub Actions pipelines that run tests and build Docker images on every pull request

Software Engineering Intern - Harborline Robotics (May 2021 - Aug 2021)
- Wrote Python scripts that parse robot telemetry logs and flag sensor dropouts, processing 1.2 million log lines per day
- Added a Flask REST API endpoint and a small web page for viewing flagged dropouts

Projects
TrailNotes - Personal project (Jan 2024 - Apr 2024)
- Built semantic search over 12,000 hiking trip reports using sentence embeddings and cosine similarity
- Served results through a FastAPI backend with a 180 ms p95 search latency

PantryPal - University capstone project (Sep 2021 - May 2022)
- Led a team of 4 building a recipe recommendation web app with React, Flask, and PostgreSQL
- Deployed the app with Docker on AWS for 350 registered student users

Education
B.S. in Computer Science - Fairhaven Institute of Technology (Aug 2018 - May 2022)
- GPA 3.7/4.0; coursework in machine learning, databases, and distributed systems

Skills
Languages: Python, TypeScript, JavaScript, SQL
Frameworks: FastAPI, Flask, React, PyTorch, scikit-learn
Tools: Docker, GitHub Actions, PostgreSQL, MongoDB, Redis, AWS (S3, EC2, Lambda), Git, pytest

Certifications
AWS Certified Cloud Practitioner - Amazon Web Services (Mar 2024)
`,
  },
  {
    label: "LinkedIn profile",
    source_type: "linkedin",
    text: `Jordan Rivera
Machine Learning Engineer at Brightloom Labs
Columbus, Ohio, United States
jordan.rivera@example.com

About
I build machine learning features that people use every day: search, classification, and question answering. Before moving into machine learning I spent two years as a product-focused software engineer, so I care about tests, latency, and clear APIs as much as model quality.

Experience
Machine Learning Engineer - Brightloom Labs (Aug 2024 - Present)
- Lead engineer for the support assistant, which answers customer questions from the company help center
- Added hybrid keyword and embedding retrieval, improving top-5 retrieval recall from 0.71 to 0.84 on the internal evaluation set
- Run weekly error-analysis reviews with 3 support team leads to decide which answer failures to fix first

Software Engineer - Quillfeather Software (Jun 2022 - Jul 2024)
- Reduced median API response time from 420 ms to 290 ms by adding Redis caching and PostgreSQL indexes
- Moved the customer export feature from a cron script to a queued worker, removing 2 hours of manual support work per week
- Mentored 2 new engineers through their first quarter on the team

Software Engineering Intern - Harborline Robotics (May 2021 - Aug 2021)
- Summer internship on the fleet diagnostics team, working in Python and Flask

Education
B.S. in Computer Science - Fairhaven Institute of Technology (Aug 2018 - May 2022)

Skills
Python, PyTorch, FastAPI, React, TypeScript, Docker, PostgreSQL, AWS, Machine Learning, Natural Language Processing

Certifications
AWS Certified Cloud Practitioner - Amazon Web Services (Mar 2024)
`,
  },
  {
    label: "Background notes",
    source_type: "notes",
    text: `Background notes for Jordan Rivera (extra details that are not on the resume)

Experience
Volunteer Web Developer - Cedar Hollow Community Library
- Rebuilt the library events page as a static site and showed 2 librarians how to update it
- I did not write down the dates; it was part-time while I was at university

Machine Learning Engineer - Brightloom Labs (Aug 2024 - Present)
- Gave a 30-minute internal talk on measuring retrieval quality, attended by about 25 engineers
- On the on-call rotation for the support assistant one week in every 6

Projects
TrailNotes - Personal project (Jan 2024 - Apr 2024)
- Hand-labelled 60 test queries and measured recall@5 of 0.78 for the search
- Stored embeddings in MongoDB and ranked them in Python instead of using a managed vector database

Skills
Coursework exposure only: TensorFlow (one assignment in a deep learning course)
Spoken languages: English (native), Spanish (conversational)

Achievements
- Won second place out of 18 teams at Fairhaven Hack Night 2021 with the first PantryPal prototype
`,
  },
];

export const SAMPLE_JOB: { title: string; company: string; description: string } = {
  title: "Applied Machine Learning Engineer",
  company: "Fernhollow AI",
  description: `About the role
Fernhollow AI builds search and question-answering tools for customer support teams. We are hiring an Applied Machine Learning Engineer to ship retrieval and language-model features end to end, from evaluation to production APIs.

Responsibilities
- Design, build, and evaluate retrieval-augmented generation (RAG) features over large document collections
- Train and fine-tune PyTorch models for text classification and ranking
- Build and maintain Python services with FastAPI and well-tested REST APIs
- Package and ship services with Docker and automated CI pipelines
- Work with support teams to analyse failures and prioritise improvements

Requirements
- 2+ years of professional software or machine learning engineering experience
- Strong Python skills and experience building REST APIs with FastAPI or Flask
- Hands-on experience with PyTorch
- Experience with embeddings, semantic search, or retrieval-augmented generation (RAG)
- Experience building evaluation sets or offline evaluation for machine learning or LLM features
- Experience with Docker
- Working knowledge of SQL and a relational database such as PostgreSQL

Preferred qualifications
- Front-end experience with React and TypeScript
- Experience with AWS
- Experience with CI/CD tools such as GitHub Actions
- Bachelor's degree in Computer Science or a related field`,
};

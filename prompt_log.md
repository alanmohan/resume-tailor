# Prompt log

## 1. Tools and models

| Tool / model | Used for | Why |
|---|---|---|
| Claude Code (model Claude Opus 5.5), with Ultracode | Building the whole application from `IMPLEMENTATION_SPEC.md`, testing, review, deployment steps | One session could plan an interface contract, then run backend, frontend, fixture, integration, end-to-end and review agents in parallel |
| Render MCP connector (inside Claude Code) | Creating the two Render services, setting non-secret environment variables, triggering deploys, reading deploy logs | To allow Claude to directly interact with Render for project setup and deployments |
| GitHub CLI (`gh`) | Creating the public repository and pushing | Render deploys from a Git repository |
| OpenAI `gpt-6-luna` and `text-embedding-3-small` | The application's own extraction, job analysis, generation and embeddings | Low-cost model for testing purposes |
| Playwright (Chromium) | Local end-to-end tests and a real-browser walk-through of the deployed site | Allows Claude to test the app in the browser and interact with the components of the app to test them out |
| ChatGPT (GPT-6.1-sol, medium effort) | Refining the feature ideas and writing `IMPLEMENTATION_SPEC.md` | To decide which features to build and to specify the frontend and backend frameworks, the LLM API provider and the database |

## 2. Prompt log

### Prompts sent to GPT-6.1-sol

**Prompt 1**

> I am planning to create a resume generator app, which generates and customizes resumes for each job description. it take the users resume, CV, linkedin profile, bio data, etc, builds a knowledge base about the user and based on a provided job description, generates a custom resume , cover letter grounded in the user's profile. job match and other metrics can also be added, along with other features like trackers for tracking the status of different job applications, suggested interview questions to prepare.

**Prompt 2**

> ok create a short project proposal with the features we plan to adding. also include the extra ones that may be added if time permits

**Prompt 3**

> can we also add a rag architecture or a graph db?

**Prompt 4**

> ok prepare a detailed spec. include the core features and RAG architecture. also include all of the optional features. frontend UI should use a modern react ui library component. backend can be fastapi or anyting pyton based. for DB, I want a noSQL db. also plan for deployment to render and include instructions for proper testing locally and deployment

**Prompt 5**

> ok among the optional features, remove linkedin import and job url import and the graph based retrieval related features. also mention use openai models. (low cost models like gpt-6-luna or gpt-5.6-terra). API key will be in .env of the project. also ask it to refer to the experience master file (wil be added in project root) which would contain a sample data that codex can use while testing. also ask it to retrieve some job descriptions (relevant to the sample profile) for testing. also create a folder within the effective coding with ai folder and add this spec doc

Final output: `IMPLEMENTATION_SPEC.md` in the project folder.

### Prompts sent to Claude Code (Opus 5.5 High with Ultracode)

**Prompt 1** (2026-10-07, about 3:10 PM ET):

> Go through @IMPLEMENTATION_SPEC.md document and implement the Resume Generator Application.
> Also, use the connected render MCP to deploy the application (account is free tier) and test on the deployed version

**Prompt 2** - answers to three multiple-choice questions the assistant asked before deploying:

- Database: "I'll add it to .env (Recommended)"
- GitHub repository: "Public: resume-tailor (Recommended)"
- Render workspace: "Yes, use Alan's workspace (Recommended)"

**Prompt 3:**

> I have added the MongoDB URI and the OpenAI API key and redployed however in the MongoDB I need to allow the IP addresses so far I've only added the IP of my, my local machine how do I get the IP of the backend server of render and add that as well to the allowed IP for MongoDB. Note i have also added mongodb credentials in this env

**Prompt 4:**

> made the changes, test again

**Prompt 5:**

> few changes I want to make to the workflow:
> Combine the review job requirements and generate resume step. after the job description has been entered by the user, run both one after the other (show user the progress of the job). the final result should be the generated resume, cover letter and show the extracted job requirements in another menu tab but dont make it editable. the edit requirement feature is not too useful and i pretty much want it removed.
>
> some more changes:
> the generated documents should be downloaded to device, not printed.

**Prompt 6:**

> i made some small changes to the frontend react, ensure those get pushed as well

## 3. Places where the AI got something wrong during the session

- **Secrets.** The assistant tried to read `OPENAI_API_KEY` from `.env` and pass
  it to Render through the connector. A safety check in Claude Code blocked reading
  the credential, so I had to enter both secrets in the Render
  dashboard myself. The assistant also told me to then put the Atlas string in
  `.env` for it to pick up, which then had to be corrected.
  The agent should have been instructed not to read secrets from `.env` and ask the user to enter them in the Render dashboard instead. Luckily, the Claude Code safety check prevented the secret from being read.

- **Grounding holes in the first implementation.** The integration agent fed the
  validators deliberately dishonest model output and found three ways an invented
  claim could pass: the role title in the stored evidence text counted as context
  for a number ("Backend engineer who reduced cloud costs by 20%" passed against a
  test-coverage statement), the whole evidence sentence counted as context for a
  number, and the model's coverage rationale was shown unchecked. All three were
  fixed with tests. This fix was made by the supervisor agent itself, not by me, but it goes to show that having multiple agents with different roles, working together and checking each other, is important to catch these issues.

## 4. Code changes I made myself

I made changes to the frontend React code, to clean up the UI and remove some unnecessary text. I also made some changes to the backend code to fix some issues with the grounding of the generated documents. I also configured the environment secrets in the Render dashboard, and added the MongoDB Atlas IP address of the backend server to the allowed IPs in the Atlas dashboard.

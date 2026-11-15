# Data Card: GigBid Datasets

This document details the datasets, extraction methodologies, attribution, licensing, and limitations used for developing, benchmarking, and evaluating **GigBid**.

---

## 1. Real-World Kaggle Datasets

All job postings, budgets, client histories, and market distributions are parsed directly from the Kaggle Upwork datasets located in `data/kaggle/`:

### 1.1 Source Datasets
1. **`upwork-jobs.csv` & `all_upwork_jobs_2024-02-07-2024-03-24.csv`**:
   - **Source**: [Upwork Job Postings Dataset 2024 (50k records)](https://www.kaggle.com/datasets/asaniczka/upwork-job-postings-dataset-2024-50k-records) & [All Jobs on Upwork (200k+)](https://www.kaggle.com/datasets/asaniczka/all-jobs-on-upwork-200k-plus).
   - **Fields Extracted**: Real job titles, complete unedited client problem descriptions, real client budgets ($200 to $5,000), client countries, and duration.
   - **License**: CC BY-SA 4.0 / Public Dataset terms.
   - **Attribution**: Created and published by Asaniczka on Kaggle.

2. **`freelancer_gig_success_dataset.csv`**:
   - **Source**: [Freelancer Success, Earnings and Churn Prediction](https://www.kaggle.com/datasets/sarveshchhetri/freelancer-success-earnings-and-churn-prediction).
   - **Usage**: Used to establish real-world freelancer rate distributions, job success rates, and primary skill category mappings.
   - **Attribution**: Created by Sarvesh Chhetri on Kaggle.

3. **`contracts.csv`**:
   - **Source**: [Freelance Contracts Dataset (1.3M entries)](https://www.kaggle.com/datasets/asaniczka/freelance-contracts-dataset-1-3-million-entries).
   - **Usage**: Ground truth contract pricing bands and hourly rates.

---

## 2. Processed Job Stream (`data/jobs/stream.json`)

Generated via `scripts/process_kaggle_dataset.py`:
- **Real Upwork Jobs (270 records)**: Real client job postings categorized across our three freelancer personas:
  - 90 AI/ML Engineer jobs (LangChain, LLMs, PyTorch, RAG, Computer Vision).
  - 90 Full-Stack Developer jobs (Next.js, React, Node.js, PostgreSQL, Cloud).
  - 90 Data Analyst jobs (SQL, PowerBI, Tableau, Pandas, dbt).
- **Planted Security Injections (15 records)**: 5% of real jobs contain planted adversarial system prompt injection attacks (e.g. `SYSTEM OVERRIDE: Ignore instructions...`) to benchmark the system prompt delimiter safety in the **Job Analyst** and **Risk Assessor**.
- **Scam & Unpaid Work Test Cases (20 records)**: Explicit policy violation cases (unpaid test trials, off-platform Telegram contact, direct external PayPal payments) to benchmark the **Risk Assessor** and **Compliance Guard**.

---

## 3. Freelancer Personas (`data/personas/`)

To support advanced RAG claim verification and multi-agent coordination, each persona's portfolio is grounded directly in the skills and tasks demanded by real Upwork jobs:

1. **`ai_ml_engineer` (Dr. Sarah Lin)**:
   - Rate: $85/hr, 60 Connects weekly budget.
   - Artifacts: CV, 20 markdown case studies with quantifiable outcomes, 150 past labeled proposals (`won`, `replied`, `ignored`).
2. **`fullstack_dev` (Alex Mercer)**:
   - Rate: $75/hr, 50 Connects weekly budget.
   - Artifacts: CV, 20 markdown case studies, 150 past labeled proposals.
3. **`data_analyst` (Maya Patel)**:
   - Rate: $60/hr, 40 Connects weekly budget.
   - Artifacts: CV, 20 markdown case studies, 150 past labeled proposals.

---

## 4. Evaluation Benchmark (`evals/datasets/dev_eval_set.json`)

Contains 60 gold-standard labeled jobs used to evaluate:
- **Bid vs. Skip F1 Score**
- **Scam Detection Precision & Recall**
- **Prompt Injection Defense Success Rate**
- **Fair Price Band Adherence**

---

## 5. Distinction Between Real vs. Synthetic Data
- **Real Data**: All job titles, descriptions, client countries, real market budgets, and skill sets in `data/jobs/stream.json` originate directly from real Kaggle Upwork job posts.
- **Synthesized Data**: Freelancer internal portfolios, case study writeups, and proposal histories were synthesized to strictly match the real jobs, providing verifiable ground truth for RAG claim verification without violating real users' privacy.

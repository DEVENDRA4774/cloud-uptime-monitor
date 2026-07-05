# Project Charter - Cloud Uptime Monitor V2

## 1. Project Overview & Context

- **Project Title**: Cloud Uptime Monitor V2
- **Executive Summary**: A web-based uptime monitoring platform built with Python, Flask, and SQLite. It continuously checks website endpoints, records response latencies, warns of SSL certificate expirations, publishes real-time alerts via Discord and AWS SNS, and performs S3 database backups. Runs locally or deployed on AWS EC2.
- **Problem Statement**: Freshers and small teams lack a lightweight, secure, and visually appealing server monitoring system that connects directly to AWS services and alerts stakeholders immediately.

## 2. Objectives & Success Criteria

- **Project Goals**:
  1. Monitor target URLs 24/7 with customizable check loops.
  2. Send alerts to Discord and AWS SNS instantly on downtime.
  3. Keep the SQLite database backed up to AWS S3 daily.
  4. Display status, SSL health, and check timeline in a responsive dark/light mode dashboard.
- **Success Metrics**:
  - 100% unit tests pass (`pytest` green).
  - Alert notifications dispatched in less than 3 seconds after downtime detection.
  - Interactive canvas chart response time coordinates render correctly.

## 3. Project Scope

- **In-Scope Items**:
  - Flask web API endpoints and background thread monitoring loop.
  - SQLite logs storage with dynamic `ssl_days` tracking.
  - AWS SNS Alerting (SMS/Email) and S3 Database backups.
  - Premium Dark/Light mode theme UI dashboard.
  - 20-check horizontal uptime timeline dot-grid per website.
  - SSL certificate expiration date resolution.
- **Out-of-Scope Items**:
  - Complex Kubernetes clustering.
  - User authentication and multi-tenant accounts.
  - Premium SMS charging logic.

## 4. Roles & Responsibilities

- **Project Sponsor**: Devendra (User)
- **Project Manager**: Antigravity AI
- **Key Stakeholders**: Developers, Assessors, Site administrators.

## 5. Estimates & Timeline Baseline

- **High-Level Schedule**:
  - **Phase 1**: Core monitor logic & SQLite database (Completed).
  - **Phase 2**: Flask web server & basic HTML dashboard (Completed).
  - **Phase 3**: Upgrade to 10/10 with SSL Expiry, AWS SNS, dark mode, interactive chart, and scroll container (Completed).
- **Preliminary Budget**: Free-tier cloud infrastructure budget (AWS EC2, S3, SNS).

## 6. Assumptions & Constraints

- **Assumptions**:
  - Target domains support HTTP request inspections.
  - AWS IAM credentials or Instance Roles are configured correctly.
- **Constraints**:
  - No massive JavaScript frameworks used (pure HTML5 canvas and Vanilla JS).
  - Single-node deployment (Flask server on EC2).

## 7. Risks & Mitigation

- **Initial Risks**:
  1. **SSRF vulnerabilities**: Mitigated by blacklisting private/localhost IPs in URL validation.
  2. **AWS S3/SNS API throttles**: Mitigated by catch-all exception blocks preventing app crashes.

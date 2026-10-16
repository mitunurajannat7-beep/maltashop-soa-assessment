# Evidence capture plan

Use genuine output from the implementation. Preserve original screenshots and accompany each with the tested commit SHA when available. Never insert tokens, passwords or personal information into screenshots.

## Required evidence

1. **Passing CI run (Section 3).** Show repository name, workflow name, successful status, identifiable commit and completed jobs. Supply the actual workflow file in the repository. Passing automated CI is distinct from an approved production release.
2. **Order request and response (Section 6.3).** Show POST endpoint, request body, relevant request headers, actual HTTP status and returned order data. Include an explicit test correlation ID so it can be found in logs. A PowerShell helper should display the request and response together.
3. **Broker event consumed (Section 6.3).** Show the Notification service consuming the event emitted by the tested order, including order ID and event ID.
4. **Matching correlation IDs (Section 6.3).** Show Order and Notification log records with the same correlation ID. This may share a screenshot with item 3 if all required details are legible.

Three screenshots may cover these four requirements. Add screenshots when needed for legibility rather than forcing unreadable output into three images.

## Additional engineering verification

Capture machine-readable results for happy path, duplicate request, duplicate event, rejected payment compensation and insufficient inventory. Do not claim that a test ran until its actual output is checked. These results strengthen the reflection even if not all are pictured in the report.

## Not assessment evidence

Hardware specifications, Docker installation, Docker version and hello-world output are setup checks only. They do not establish that the project works.

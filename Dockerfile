FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

# Set the working directory
WORKDIR /app

# Copy the application source code and configs
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY config/ ./config/
# Note: live_scan.py is useful for local debugging, but the CLI wcag-scanner is the primary entrypoint
COPY live_scan.py ./

# Install the Python package and its dependencies
RUN pip install --no-cache-dir -e .

# The Playwright base image already has browsers installed, so we don't need `playwright install chromium` again,
# but we can run it just in case it's missing or an update is needed to match the python package.
RUN playwright install chromium

# Create the artifacts directory where scans output will be saved
RUN mkdir -p /app/artifacts

# Define the default command to run when the container starts.
# You can override these arguments when running the container or via Kubernetes args.
ENTRYPOINT ["wcag-scanner"]
CMD ["--url", "https://example.gov", "--domain", "example.gov"]
